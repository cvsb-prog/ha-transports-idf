// Tronçons coupés et arrêts non desservis sur le plan d'une ligne (logique de la carte, sans navigateur).
// Usage : node tests/carte/reseau.cjs → « ok » ou une erreur.
const fs = require("fs");
const path = require("path");
const assert = require("assert");
const src = fs.readFileSync(path.join(__dirname, "../../custom_components/transports_idf/www/transports-card.js"), "utf8");
const debut = src.indexOf("const interruptions = (messages)");
const fin = src.indexOf("// Nom d'arrêt comparable");
const nom = src.indexOf("const nomArret");
eval(src.slice(debut, fin) + src.slice(nom, src.indexOf("\n", nom)) + ";globalThis.C = { interruptions, etatReseau };");

// Ligne A–B–C–D–E (tous les 1 km vers l'est), branche C–F, et un tracé direct B–D parallèle (voies rapides).
const pos = { A: 0, B: 1, C: 2, D: 3, E: 4 };
const arrets = ["A", "B", "C", "D", "E", "F"].map((n, i) => ({ id: `sa:${n}`, nom: `Gare ${n}`, lat: n === "F" ? 48.81 : 48.8, lon: 2.3 + (n === "F" ? 2 : pos[n]) * 0.0137 }));
const seg = (a, b, decale = 0) => {
  const p = arrets[a], q = arrets[b];
  return [0, 0.25, 0.5, 0.75, 1].map((t) => [p.lat + (q.lat - p.lat) * t + decale * Math.sin(Math.PI * t), p.lon + (q.lon - p.lon) * t]);
};
const aretes = [[0, 1], [1, 2], [2, 3], [3, 4], [2, 5], [1, 3]];
const traces = aretes.map(([a, b], k) => seg(a, b, k === 5 ? 0.004 : 0)); // B–D passe à ~440 m de C
const bout = (n) => ({ noms: [`gare ${n.toLowerCase()}`], zones: [`sa:${n}`] });
const etat = (messages) => C.etatReseau(arrets, traces, C.interruptions(messages), aretes);
const noms = (r) => [...r.gris.keys()].map((i) => arrets[i].nom).sort();
const coupees = (r) => [...r.coupees].map((k) => k.split("|").map((i) => arrets[i].nom.slice(-1)).join("")).sort();

// 1. Tronçon coupé A → E : tout l'entre-deux grisé (C aussi, malgré le direct B–D), pas la branche F.
let r = etat([{ niveau: "interrompu", portee: "troncon", coupures: [{ de: bout("A"), vers: bout("E") }] }]);
assert.deepStrictEqual(noms(r), ["Gare B", "Gare C", "Gare D"]);
assert.ok(!coupees(r).includes("CF"), "la branche n'est pas coupée");
// 2. Gares sautées (trains passants) B → D : seule C est sautée ; le sens est gardé.
r = etat([{ niveau: "perturbe", portee: "arrets", sauts: [{ de: bout("B"), vers: bout("D"), direction: "Gare E" }] }]);
assert.deepStrictEqual(noms(r), ["Gare C"]);
assert.strictEqual(r.gris.get(2), "Gare E");
assert.deepStrictEqual(coupees(r), ["BC", "BD", "CD"]); // le direct B–D aussi : tout le tronçon est en pointillés
// 3. Arrêt cité seul (de = vers) et arrêt non desservi cité par son nom : grisés, pointillés jusqu'aux voisins.
r = etat([{ niveau: "perturbe", portee: "arrets", sauts: [{ de: bout("E"), vers: bout("E") }] }]);
assert.deepStrictEqual(noms(r), ["Gare E"]);
r = etat([{ niveau: "perturbe", portee: "arrets", arrets_touches: ["gare f"] }]);
assert.deepStrictEqual(noms(r), ["Gare F"]);
assert.deepStrictEqual(coupees(r), ["CF"]);
// 4. Un tronçon perturbé (pas interrompu) ne coupe rien.
r = etat([{ niveau: "perturbe", portee: "troncon", coupures: [{ de: bout("A"), vers: bout("E") }] }]);
assert.deepStrictEqual(noms(r), []);
// 5. Rien au-delà des bouts : B → D ne grise ni A ni E.
r = etat([{ niveau: "interrompu", portee: "troncon", coupures: [{ de: bout("B"), vers: bout("D") }] }]);
assert.deepStrictEqual(noms(r), ["Gare C"]);
console.log("ok");
