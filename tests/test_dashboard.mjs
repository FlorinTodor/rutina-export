// Ejecuta el dashboard en un DOM real y recorre las tres secciones.
// Cazó un bug que habría publicado la página rota: la extracción del <title>
// para la cabecera se llevaba por delante una línea del JavaScript.
//   npm install jsdom && node tests/test_dashboard.mjs [ruta/al/index.html]
import { JSDOM } from "jsdom";
import fs from "fs";

const html = fs.readFileSync(
  process.argv[2] ?? "docs/dashboard/index.html", "utf8");

const errors = [];
const dom = new JSDOM(html, { runScripts: "dangerously", pretendToBeVisual: true });
dom.virtualConsole.on("jsdomError", e => errors.push("jsdomError: " + e.message));
dom.window.addEventListener("error", e => errors.push("error: " + e.message));

await new Promise(r => setTimeout(r, 400));
const { document } = dom.window;

function check(label) {
  const rows = document.querySelectorAll("#list .row").length;
  const svgs = document.querySelectorAll("svg").length;
  console.log(`  ${label.padEnd(12)} svg=${String(svgs).padStart(3)}  filas=${rows}`);
}

console.log("=== FUERZA (por defecto) ===");
check("inicial");
console.log("  titulo detalle:", document.querySelector("#detail h2")?.textContent);
console.log("  tiles:", [...document.querySelectorAll("#detail .tile .l")].map(x=>x.textContent).join(", "));
console.log("  filas tabla:", document.querySelectorAll("#detail tbody tr").length);
console.log("  gif:", document.querySelector("#detail .dgif")?.getAttribute("src")?.slice(-28));

for (const tab of ["actividad", "cuerpo"]) {
  console.log(`\n=== ${tab.toUpperCase()} ===`);
  document.querySelector(`.tab[data-t="${tab}"]`).click();
  await new Promise(r => setTimeout(r, 200));
  const sec = document.querySelector("#sec-" + tab);
  console.log("  visible:", !sec.hidden);
  [...sec.querySelectorAll(".card")].forEach(c => {
    const h = c.querySelector("h3")?.textContent ?? "(tiles)";
    const svg = c.querySelectorAll("svg").length;
    const blank = c.querySelector(".blank")?.textContent.trim().slice(0, 42);
    console.log(`   · ${h.padEnd(20)} svg=${svg}` + (blank ? `  VACIO: ${blank}` : ""));
  });
}

/* Solo una seccion visible a la vez.

   Cazado en uso: el display de una clase gana al display:none que el
   navegador da a [hidden], asi que #sec-fuerza (class="grid") seguia
   pintandose debajo de Actividad y de Cuerpo, con su buscador y su lista
   entera de ejercicios.

   Se comprueba la REGLA CSS y no el display calculado porque jsdom no modela
   esa parte de la cascada: da "none" en los dos casos, con el arreglo y sin
   el, asi que un test sobre getComputedStyle pasaria siempre y daria falsa
   confianza. Verificado quitando la regla a mano. */
console.log("\n=== una sola seccion a la vez ===");
{
  const css = [...document.querySelectorAll("style")].map(x => x.textContent).join("\n");
  const regla = /\[hidden\]\s*{[^}]*display\s*:\s*none\s*!important/.test(css);
  console.log(`  regla [hidden]{display:none!important}: ${regla ? "sí" : "NO"}`);
  if (!regla) {
    errors.push("falta [hidden]{display:none!important}: las secciones con " +
                "clase de display (como #sec-fuerza) se veran en todas las pestanas");
  }

  // y que cada pestana marca hidden en las demas
  const SECS = ["resumen", "fuerza", "actividad", "cuerpo"];
  for (const t of SECS) {
    document.querySelector(`.tab[data-t="${t}"]`).click();
    const marcadas = SECS.filter(k => !document.querySelector("#sec-" + k).hidden);
    const ok = marcadas.length === 1 && marcadas[0] === t;
    console.log(`  ${t.padEnd(10)} sin hidden: ${marcadas.join(", ")}${ok ? "" : "   <-- MAL"}`);
    if (!ok) errors.push(`la pestana ${t} deja ${marcadas.length} secciones sin hidden`);
  }
}

console.log("\n=== busqueda y orden ===");
document.querySelector(`.tab[data-t="fuerza"]`).click();
const q = document.querySelector("#q");
q.value = "press"; q.dispatchEvent(new dom.window.Event("input"));
console.log("  buscando 'press':", document.querySelectorAll("#list .row").length, "resultados");
const sel = document.querySelector("#sort");
sel.value = "stalled"; sel.dispatchEvent(new dom.window.Event("change"));
console.log("  orden 'estancado', primero:", document.querySelector("#list .rname")?.textContent);

/* Los tooltips responden en TODAS las gráficas.

   Cazado en uso: hover() buscaba el .chartbox dentro de lo que le pasaban,
   pero las gráficas de ejercicio le pasan el .chartbox mismo. cb quedaba en
   null y getBoundingClientRect reventaba en cada mousemove, así que esas
   gráficas eran mudas: ni tooltip ni cruz. Las de cuerpo y actividad sí
   funcionaban, por eso pasó desapercibido.

   Se mueve el ratón por encima de cada zona sensible y se comprueba que no
   salte ningún error y que el tooltip se rellene. */
console.log("\n=== tooltips en las gráficas ===");
{
  const antes = errors.length;
  let probadas = 0, mudas = 0;
  for (const t of ["fuerza", "actividad", "cuerpo"]) {
    document.querySelector(`.tab[data-t="${t}"]`).click();
    for (const hit of document.querySelectorAll(`#sec-${t} .hit, #detail .hit`)) {
      const box = hit.closest(".chartbox");
      const tip = box?.querySelector(".tip");
      if (!tip) continue;
      probadas++;
      hit.dispatchEvent(new dom.window.MouseEvent("mousemove",
        { bubbles: true, clientX: 200, clientY: 100 }));
      if (!tip.innerHTML.trim()) mudas++;
    }
  }
  const rotos = errors.length - antes;
  console.log(`  ${probadas} gráficas probadas · ${mudas} sin tooltip · ${rotos} con error`);
  if (mudas) errors.push(`${mudas} gráficas no muestran tooltip al pasar el ratón`);
}

console.log("\nerrores:", errors.length ? errors : "ninguno");

// Sin esto el test imprimia los fallos y salia con exito igualmente, asi que
// en CI pasaba siempre por muy roto que estuviera el dashboard.
process.exit(errors.length ? 1 : 0);
