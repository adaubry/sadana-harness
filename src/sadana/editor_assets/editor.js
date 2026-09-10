/* The page: draw what the server sent, post back what was drawn.
 *
 * docs/tasks/PLUGIN-EDITOR-01-draw-wire-and-save/spec.md. This file holds no
 * logic that could live in Python (CLAUDE.md), because nothing in this
 * repository can test JavaScript. In particular it never computes a layout:
 * positions arrive from the server, a step added here is parked at a fixed
 * offset until the next server answer places it properly, and validation is
 * whatever the server said it was.
 *
 * The one piece of state kept here is `moved` — boxes the person dragged
 * during this session. Positions are deliberately not stored anywhere (see
 * intent.md's Open questions and the decision recorded in spec.md), so this
 * keeps a drag from snapping back on every save while staying honest that a
 * reload starts over. It is presentation state, not logic.
 */

const BOX_W = 150;
const BOX_H = 56;

let open = null; // {name, manifest, positions, problems, waiting}
let palette = []; // [{kind, uses, runnable}]
let selected = null; // a step's name
let linking = false; // waiting for a click on the step to join to
const moved = {}; // name -> [x, y], this session only

const $ = (id) => document.getElementById(id);
const api = async (method, path, body) => {
  const response = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `${method} ${path} failed`);
  return payload;
};

const node = (name) => open.manifest.nodes.find((n) => n.name === name);
const kindInfo = (kind) => palette.find((k) => k.kind === kind);
const at = (name) => moved[name] || open.positions[name];

// ── loading ──────────────────────────────────────────────────────────────

async function loadPlugins(current) {
  const { plugins } = await api("GET", "/api/plugins");
  const list = $("plugin-list");
  list.replaceChildren();
  for (const plugin of plugins) {
    const button = document.createElement("button");
    button.type = "button";
    button.append(plugin.name);
    const status = document.createElement("span");
    status.className = "status";
    status.textContent = plugin.status;
    button.append(status);
    if (plugin.name === current) button.setAttribute("aria-current", "true");
    button.addEventListener("click", () => openPlugin(plugin.name));
    const item = document.createElement("li");
    item.append(button);
    list.append(item);
  }
}

async function openPlugin(name) {
  open = await api("GET", `/api/plugins/${name}`);
  selected = null;
  linking = false;
  for (const key of Object.keys(moved)) delete moved[key];
  $("open-name").textContent = name;
  await loadPlugins(name);
  draw();
}

// ── drawing ──────────────────────────────────────────────────────────────

function svg(tag, attrs, text) {
  const element = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, value] of Object.entries(attrs)) element.setAttribute(key, value);
  if (text !== undefined) element.textContent = text;
  return element;
}

function arrow(from, to, label) {
  const [x1, y1] = at(from);
  const [x2, y2] = at(to);
  const start = [x1 + BOX_W, y1 + BOX_H / 2];
  const end = [x2, y2 + BOX_H / 2];
  const bend = (start[0] + end[0]) / 2;
  const path = svg("path", { d: `M ${start[0]} ${start[1]} C ${bend} ${start[1]}, ${bend} ${end[1]}, ${end[0]} ${end[1]}` });
  const parts = [path];
  if (label) parts.push(svg("text", { x: bend, y: (start[1] + end[1]) / 2 - 4, "text-anchor": "middle" }, label));
  return parts;
}

function draw() {
  const boxes = $("boxes");
  const arrows = $("arrows");
  boxes.replaceChildren();
  arrows.replaceChildren();
  if (!open) return;

  for (const step of open.manifest.nodes) {
    if (step.next && node(step.next)) arrows.append(...arrow(step.name, step.next));
    for (const port of step.ports || []) {
      if (node(port)) arrows.append(...arrow(step.name, port, port));
    }
  }

  for (const step of open.manifest.nodes) {
    const [x, y] = at(step.name);
    const group = svg("g", { class: "box", transform: `translate(${x} ${y})` });
    const waitingFor = open.waiting.find((w) => w.step === step.name);
    if (step.name === selected) group.classList.add("selected");
    if (waitingFor) group.classList.add("needs-code");
    if (!kindInfo(step.kind).runnable) group.classList.add("not-runnable");
    group.append(svg("rect", { width: BOX_W, height: BOX_H }));
    group.append(svg("text", { class: "name", x: 10, y: 22 }, step.name));
    group.append(svg("text", { class: "kind", x: 10, y: 40 }, step.kind));
    if (!kindInfo(step.kind).runnable) group.append(svg("text", { class: "flag", x: 10, y: 52 }, "not runnable yet"));
    else if (waitingFor) group.append(svg("text", { class: "flag", x: 10, y: 52 }, `needs ${waitingFor.for}`));
    group.addEventListener("pointerdown", (event) => onBoxPointerDown(event, step.name));
    boxes.append(group);
  }

  drawPanel();
  drawStatus();
}

function drawStatus() {
  const problems = $("problems");
  const needs = $("needs-code");
  problems.replaceChildren();
  needs.replaceChildren();
  for (const problem of open.problems) {
    const item = document.createElement("li");
    item.textContent = problem;
    problems.append(item);
  }
  for (const item_ of open.waiting) {
    const item = document.createElement("li");
    item.textContent = `${item_.step} needs ${item_.for}`;
    needs.append(item);
  }
}

function drawPanel() {
  const editor = $("step-editor");
  if (!selected || !node(selected)) {
    editor.hidden = true;
    return;
  }
  const step = node(selected);
  const uses = kindInfo(step.kind).uses;
  editor.hidden = false;
  $("field-name").value = step.name;
  $("field-kind").value = step.kind;
  $("field-skill").value = step.skill || "";
  $("field-body").value = step.body || "";
  $("label-skill").hidden = !uses.includes("skill");
  $("label-body").hidden = !uses.includes("body");
  $("body-note").hidden = !open.waiting.some((w) => w.step === step.name && w.for === "code");
  $("link").textContent = uses.includes("ports") ? "Add a branch…" : "Join to another step…";
  $("link-controls").hidden = step.kind === "stop";
}

// ── editing ──────────────────────────────────────────────────────────────

function onBoxPointerDown(event, name) {
  if (linking) {
    joinTo(name);
    return;
  }
  selected = name;

  // The move and up listeners go on `window`, not on the box, and there is no
  // `setPointerCapture` here. A cold review found the obvious version broken:
  // `draw()` calls `replaceChildren()`, so by the time the handler reached
  // `setPointerCapture` the element it held had been detached from the
  // document, which the Pointer Events spec requires to throw
  // `InvalidStateError` — every click threw, and dragging never worked at
  // all. Listening on a node no redraw replaces sidesteps the whole problem.
  const [startX, startY] = at(name);
  const originX = event.clientX;
  const originY = event.clientY;
  const onMove = (moveEvent) => {
    moved[name] = [startX + moveEvent.clientX - originX, startY + moveEvent.clientY - originY];
    draw();
  };
  const onUp = () => {
    window.removeEventListener("pointermove", onMove);
    window.removeEventListener("pointerup", onUp);
  };
  window.addEventListener("pointermove", onMove);
  window.addEventListener("pointerup", onUp);
  draw();
}

function joinTo(target) {
  const step = node(selected);
  linking = false;
  document.body.classList.remove("linking");
  if (!step || target === selected) return draw();
  if (kindInfo(step.kind).uses.includes("ports")) {
    if (!step.ports.includes(target)) step.ports.push(target);
  } else {
    step.next = target;
  }
  draw();
}

async function stepOp(payload) {
  // Adding, renaming and deleting are graph rewrites — a rename has to follow
  // every arrow, every route port and the entry's own start — so they live in
  // Python where they are tested, and this asks for one and redraws the
  // answer. See CLAUDE.md on a browser surface holding no logic.
  try {
    open = await api("POST", `/api/plugins/${open.name}/steps`, payload);
    await loadPlugins(open.name);
    draw();
  } catch (error) {
    $("saved-note").textContent = error.message;
  }
}

async function save() {
  $("saved-note").textContent = "saving…";
  try {
    open = await api("PUT", `/api/plugins/${open.name}`, open.manifest);
    $("saved-note").textContent = "saved";
    await loadPlugins(open.name);
    draw();
  } catch (error) {
    $("saved-note").textContent = error.message;
  }
}

// ── wiring ───────────────────────────────────────────────────────────────

async function start() {
  const { kinds } = await api("GET", "/api/kinds");
  palette = kinds;
  for (const target of [$("add-kind"), $("field-kind")]) {
    for (const { kind, runnable } of palette) {
      const option = document.createElement("option");
      option.value = kind;
      option.textContent = runnable ? kind : `${kind} (not runnable yet)`;
      target.append(option);
    }
  }
  await loadPlugins(null);
}

$("new-plugin").addEventListener("submit", async (event) => {
  event.preventDefault();
  const name = $("new-name").value.trim();
  if (!name) return;
  try {
    await api("POST", "/api/plugins", { name });
    $("new-name").value = "";
    await openPlugin(name);
  } catch (error) {
    window.alert(error.message);
  }
});

$("add-step").addEventListener("click", () => open && stepOp({ op: "add", kind: $("add-kind").value }));
$("save").addEventListener("click", () => open && save());
$("delete-step").addEventListener("click", () => {
  if (!selected) return;
  const step = selected;
  selected = null;
  stepOp({ op: "delete", step });
});
$("field-name").addEventListener("change", (event) => {
  const to = event.target.value.trim();
  if (!selected || to === selected) return;
  const step = selected;
  selected = to;
  stepOp({ op: "rename", step, to });
});
$("field-kind").addEventListener("change", (event) => {
  node(selected).kind = event.target.value;
  draw();
});
$("field-skill").addEventListener("change", (event) => {
  node(selected).skill = event.target.value || null;
});
$("field-body").addEventListener("change", (event) => {
  node(selected).body = event.target.value || null;
});
$("link").addEventListener("click", () => {
  linking = true;
  document.body.classList.add("linking");
});
$("unlink").addEventListener("click", () => {
  const step = node(selected);
  step.next = null;
  step.ports = [];
  draw();
});

start();
