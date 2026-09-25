"""
PathSense - Auto-rickshaw recognition (track-level class refinement)
Module: autorickshaw.py

COCO-trained YOLO has no auto-rickshaw class and reports autos as 'truck' / 'bus'. This module re-checks only those
boxes with zero-shot CLIP (openai/clip-vit-base-patch32 via the already-required `transformers` package; no training,
no new dependency) and relabels a *track* as 'auto-rickshaw' only after repeated agreement:

  - candidates: YOLO class truck/bus, confirmed ByteTrack id, box fully inside the image (all four borders),
    60 px <= size, width <= 45% / height <= 60% of the frame (no partial close-ups)
  - each candidate track is sampled at most every `every_s` seconds, max `max_crops` crops per frame (batched)
  - p_auto = CLIP probability mass of the auto-rickshaw prompts vs truck / bus / car / van prompts
  - a track is labelled auto-rickshaw when it has >= `min_votes` samples with mean p_auto >= `threshold`

The YOLO detector and ByteTrack are unchanged; the original class is kept in obj['yolo_class']. If CLIP cannot be
loaded, the classifier disables itself and the pipeline keeps the YOLO labels. Validated by eye on 60 Bangalore
truck/bus crops (about 47 were autos): at p_auto > 0.6, 46 autos recognised and 1 false positive (a small yellow
goods tempo); the pipeline uses the stricter 0.75 plus multi-sample voting and ignores crops narrower than 60 px. It is a zero-shot estimate, not ground truth.
"""

from collections import defaultdict
from typing import Any, Dict, List, Optional

import cv2
import numpy as np

AUTO_PROMPTS = ["a photo of an auto rickshaw", "a photo of a three-wheeler tuk-tuk"]


def _emb(out):
    """CLIP get_*_features returns a tensor (transformers 4.x) or an output object with pooler_output (5.x)."""
    return out if hasattr(out, "norm") else out.pooler_output
OTHER_PROMPTS = ["a photo of a truck", "a photo of a bus", "a photo of a car", "a photo of a van"]


class AutoRickshawClassifier:
    def __init__(self, fps: float = 25.0, device: Optional[str] = None, model_name: str = "openai/clip-vit-base-patch32",
                 every_s: float = 1.0, max_crops: int = 2, threshold: float = 0.75, min_votes: int = 2,
                 min_width_px: int = 60, enabled: bool = True):
        self.enabled = enabled
        self.every = max(1, int(round(every_s * (fps if fps and fps > 0 else 25.0))))
        self.max_crops, self.threshold, self.min_votes, self.min_w = max_crops, threshold, min_votes, min_width_px
        self.votes: Dict[int, List[float]] = defaultdict(list)
        self.last_sample: Dict[int, int] = {}
        self.classified_crops = 0
        self.error: Optional[str] = None
        if not enabled:
            return
        try:
            import torch
            from transformers import CLIPModel, CLIPProcessor
            self.torch = torch
            self.device = device or ("cuda:0" if torch.cuda.is_available() else "cpu")
            self.dtype = torch.float16 if self.device.startswith("cuda") else torch.float32
            self.model = CLIPModel.from_pretrained(model_name, dtype=self.dtype).to(self.device).eval()
            self.proc = CLIPProcessor.from_pretrained(model_name)
            with torch.no_grad():
                t = self.proc(text=AUTO_PROMPTS + OTHER_PROMPTS, return_tensors="pt", padding=True).to(self.device)
                te = _emb(self.model.get_text_features(**t))
                self.text = te / te.norm(dim=-1, keepdim=True)
        except Exception as e:  # keep the pipeline running with plain YOLO labels
            self.enabled, self.error = False, f"{type(e).__name__}: {e}"

    def _p_auto(self, crops: List[np.ndarray]) -> List[float]:
        torch = self.torch
        rgb = [cv2.cvtColor(c, cv2.COLOR_BGR2RGB) for c in crops]
        with torch.no_grad():
            px = self.proc(images=rgb, return_tensors="pt")["pixel_values"].to(self.device, self.dtype)
            ie = _emb(self.model.get_image_features(pixel_values=px))
            ie = ie / ie.norm(dim=-1, keepdim=True)
            probs = (self.model.logit_scale.exp() * ie @ self.text.T).float().softmax(dim=-1)
        return probs[:, :len(AUTO_PROMPTS)].sum(dim=-1).cpu().tolist()

    def update(self, frame: np.ndarray, objects: List[Dict[str, Any]], frame_idx: int):
        """Refines classes in place: confirmed tracks get class_name 'auto-rickshaw' (original kept in 'yolo_class')."""
        if not self.enabled:
            return
        h, w = frame.shape[:2]
        due = []
        for o in objects:
            tid = o.get("track_id", -1)
            if tid < 0 or o.get("class_name") not in ("truck", "bus"):
                continue
            x1, y1, x2, y2 = [int(round(v)) for v in o["bbox"]]
            # only whole, reasonably sized views: crops cut by any image border or huge close-ups (e.g. a truck front
            # filling the frame) are unreliable for zero-shot classification
            if x1 <= 2 or x2 >= w - 2 or y1 <= 2 or y2 >= h - 2 or x2 - x1 < self.min_w or y2 - y1 < self.min_w:
                continue
            if x2 - x1 > 0.45 * w or y2 - y1 > 0.6 * h:
                continue
            if frame_idx - self.last_sample.get(tid, -10**9) >= self.every:
                due.append((len(self.votes[tid]), tid, frame[max(0, y1):y2, max(0, x1):x2]))
        if due:
            due.sort(key=lambda t: t[0])  # least-sampled tracks first
            batch = due[:self.max_crops]
            for (_, tid, _), p in zip(batch, self._p_auto([c for _, _, c in batch])):
                self.votes[tid].append(float(p))
                self.last_sample[tid] = frame_idx
                self.classified_crops += 1
        for o in objects:
            v = self.votes.get(o.get("track_id", -1))
            if v and o.get("class_name") in ("truck", "bus") and len(v) >= self.min_votes and np.mean(v) >= self.threshold:
                o["yolo_class"] = o["class_name"]
                o["class_name"] = "auto-rickshaw"
                o["auto_p"] = round(float(np.mean(v)), 2)

    @property
    def confirmed_tracks(self) -> int:
        return sum(1 for v in self.votes.values() if len(v) >= self.min_votes and np.mean(v) >= self.threshold)
