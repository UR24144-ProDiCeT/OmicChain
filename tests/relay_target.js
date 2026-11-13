// Usage: node relay_target.js <relay.html> <url>  -> prints JSON {state, title, target, pwned, imgs}
const fs = require("fs"); const { JSDOM } = require("jsdom");
const html = fs.readFileSync(process.argv[2], "utf8");
const vc = new (require("jsdom").VirtualConsole)();          // silence "navigation not implemented"
const dom = new JSDOM(html, { runScripts: "dangerously", url: process.argv[3], virtualConsole: vc });
const d = dom.window.document;
console.log(JSON.stringify({ title: d.getElementById("title").textContent,
  target: d.documentElement.dataset.target || null, pwned: dom.window.PWNED || null,
  imgs: d.querySelectorAll("img").length, link: d.getElementById("continue").getAttribute("href") }));
