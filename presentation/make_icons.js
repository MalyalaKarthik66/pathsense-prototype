// Rasterise react-icons (MIT-licensed icon sets) to PNG for the PathSense deck.
const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");
const sharp = require("sharp");
const fs = require("fs");
const path = require("path");
const md = require("react-icons/md");
const fa = require("react-icons/fa");
const gi = require("react-icons/gi");
const out = process.argv[2];
fs.mkdirSync(out, { recursive: true });

const icons = {
  car: md.MdDirectionsCar, bus: md.MdDirectionsBus, truck: fa.FaTruck, moto: md.MdTwoWheeler, walk: md.MdDirectionsWalk,
  cow: gi.GiCow, auto: md.MdElectricRickshaw, cart: md.MdShoppingCart, camera: md.MdVideocam, radar: md.MdRadar,
  route: md.MdRoute, brain: md.MdPsychology, warning: md.MdWarning, hospital: md.MdLocalHospital, phone: md.MdPhone,
  location: md.MdLocationOn, check: md.MdCheckCircle, speed: md.MdSpeed, layers: md.MdLayers, visibility: md.MdVisibility,
  timeline: md.MdTimeline, web: md.MdLanguage, mobile: md.MdSmartphone, science: md.MdScience, bolt: md.MdBolt,
  shield: md.MdShield, sensors: md.MdSensors, map: md.MdMap, loop: md.MdLoop, ambulance: fa.FaAmbulance,
  crash: md.MdCarCrash, flag: md.MdFlag, upload: md.MdCloudUpload, event: md.MdEventNote, tune: md.MdTune,
  merge: md.MdMergeType, block: md.MdBlock, pothole: md.MdReport, rule: md.MdRule, rocket: md.MdRocketLaunch,
};
const colors = { n: "#1F2A44", b: "#0070C0", e: "#0F9E8E", r: "#DC2626", a: "#D97706", g: "#16A34A", w: "#FFFFFF", m: "#64748B" };

(async () => {
  for (const [name, C] of Object.entries(icons)) {
    for (const [ck, col] of Object.entries(colors)) {
      const svg = renderToStaticMarkup(React.createElement(C, { size: 256, color: col }));
      await sharp(Buffer.from(svg)).resize(256, 256).png().toFile(path.join(out, `${name}_${ck}.png`));
    }
  }
  console.log("icons:", Object.keys(icons).length);
})();
