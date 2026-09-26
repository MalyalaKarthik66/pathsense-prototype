// Rasterise technology logos (Simple Icons via react-icons, CC0/MIT) to PNG for the deck's TECH STACK panel.
// Logos are used nominatively to name the technologies the project uses.
//   node presentation/make_logos.js presentation/assets/logos     (needs react, react-dom, react-icons, sharp)
const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");
const sharp = require("sharp");
const fs = require("fs");
const path = require("path");
const si = require("react-icons/si");
const out = process.argv[2];
fs.mkdirSync(out, { recursive: true });

const logos = {
  python: [si.SiPython, "#3776AB"], pytorch: [si.SiPytorch, "#EE4C2C"], opencv: [si.SiOpencv, "#5C3EE8"],
  flask: [si.SiFlask, "#1B2A41"], javascript: [si.SiJavascript, "#E8C400"], html: [si.SiHtml5, "#E34F26"],
  css: [si.SiCss, "#1572B6"], numpy: [si.SiNumpy, "#4D77CF"], nvidia: [si.SiNvidia, "#76B900"],
  ultralytics: [si.SiUltralytics, "#042AFF"], github: [si.SiGithub, "#1B2A41"], osm: [si.SiOpenstreetmap, "#7EBC6F"],
};
(async () => {
  for (const [name, [C, col]] of Object.entries(logos)) {
    const svg = renderToStaticMarkup(React.createElement(C, { size: 256, color: col }));
    await sharp(Buffer.from(svg)).resize(256, 256).png().toFile(path.join(out, `${name}.png`));
  }
  console.log("logos:", Object.keys(logos).length);
})();
