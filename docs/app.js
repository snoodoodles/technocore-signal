"use strict";

const byId = (id) => document.getElementById(id);
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
let currentSnapshot = null;
let animationFrame = null;

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = String(text);
  return node;
}

function externalLink(label, value, className = "card-link") {
  const safe = safeExternalUrl(value);
  if (!safe) return element("span", className, "Link unavailable");
  const link = element("a", className, label);
  link.href = safe;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  return link;
}

function number(value) {
  return new Intl.NumberFormat("en").format(Number(value) || 0);
}

function percent(numerator, denominator) {
  if (!denominator) return "0%";
  return `${Math.round((Number(numerator) / Number(denominator)) * 100)}%`;
}

function ratio(value) {
  return `${Math.round((Number(value) || 0) * 100)}%`;
}

function setMetrics(snapshot) {
  const totals = snapshot.totals;
  byId("metric-messages").textContent = number(totals.messages_analyzed);
  byId("metric-signed").textContent = percent(totals.signed_messages, totals.messages_analyzed);
  byId("metric-duplicates").textContent = percent(totals.duplicate_extras, totals.messages_analyzed);
  byId("metric-coordination").textContent = number(totals.coordination_signals);
}

function meter(value) {
  const shell = element("span", "meter");
  const fill = element("i");
  fill.style.width = `${Math.min(100, Math.max(0, Number(value) * 100))}%`;
  shell.append(fill);
  return shell;
}

function setRooms(snapshot) {
  const body = byId("rooms-body");
  body.replaceChildren();
  for (const room of snapshot.rooms) {
    const row = document.createElement("tr");
    const name = element("td", null, `#${room.room}`);
    const messages = element("td", null, number(room.message_count));
    const senders = element("td", null, number(room.unique_sender_count));
    const concentration = element("td", null, ratio(room.top_sender_share));
    concentration.append(meter(room.top_sender_share));
    const duplicates = element("td", null, ratio(room.duplicate_rate));
    const coordination = element("td", null, number(room.coordination_signal_count));
    row.append(name, messages, senders, concentration, duplicates, coordination);
    body.append(row);
  }
  const scope = snapshot.collection;
  const discovery = scope.discovery === "explicit room allowlist"
    ? "Selected-room sample; not a network-wide census."
    : `Newest-room directory sample from ${number(scope.directory_total_rooms)} indexed rooms.`;
  byId("snapshot-note").textContent = `${discovery} ${number(scope.message_limit)} messages requested per room. Raw bodies were not stored.`;
}

function setMapValues(snapshot) {
  const values = byId("map-values");
  values.replaceChildren();
  for (const room of snapshot.rooms) {
    const coordinationRate = room.message_count
      ? room.coordination_signal_count / room.message_count
      : 0;
    values.append(element(
      "li",
      null,
      `#${room.room}: ${number(room.unique_sender_count)} unique senders; ${ratio(coordinationRate)} coordination rate; ${ratio(room.duplicate_rate)} exact duplicate-extra rate.`,
    ));
  }
}

function pill(text) {
  return element("span", "pill", text);
}

function setProjects(snapshot) {
  const grid = byId("projects-grid");
  grid.replaceChildren();
  if (!snapshot.projects.length) {
    const empty = element("article", "empty-state");
    const content = element("div");
    content.append(
      element("span", "card-kicker", "NO VALID SIGNAL/1 CARDS IN THIS SAMPLE"),
      element("h3", null, "The registry starts honest: empty."),
      element("p", null, "Signal does not turn ordinary promotions into projects. A server-reported signed-lane DID must deliberately publish the strict project envelope."),
      element("span", "empty-code", "SIGNAL/1 { project · repo · stage · seeking }")
    );
    empty.append(content);
    grid.append(empty);
    return;
  }
  for (const project of snapshot.projects) {
    const card = element("article", "project-card");
    const meta = element("div", "card-meta");
    meta.append(pill(project.stage), pill(`room / ${project.room}`), pill("server-reported DID"));
    card.append(
      element("span", "card-kicker", "OPT-IN PROJECT"),
      element("h3", null, project.project),
      element("p", null, project.summary),
      meta,
      element("p", "claimant", `Claimant: ${project.server_reported_did}`),
      element("p", null, `Seeking: ${project.seeking}`),
      externalLink("Repository ↗", project.repo)
    );
    grid.append(card);
  }
}

function proofRow(label, value) {
  const row = document.createElement("li");
  row.append(element("span", null, label), element("span", null, value));
  return row;
}

function setProofs(proofs) {
  const grid = byId("proofs-grid");
  grid.replaceChildren();
  for (const proof of proofs.items) {
    const card = element("article", "proof-card featured");
    const badges = element("div", "card-meta");
    badges.append(pill(proof.release.tag), pill("public repository"), pill("legacy room receipt"));
    const list = element("ul", "proof-list");
    list.append(
      proofRow("DID", proof.did),
      proofRow("Reviewed commit", proof.reviewed_commit),
      proofRow("Release target", proof.release.target_commit),
      proofRow("Technocore seq", `${proof.technocore_evidence.room} / ${proof.technocore_evidence.sequence}`),
      proofRow("Live state", proof.technocore_evidence.live_state)
    );
    card.append(
      element("span", "card-kicker", `FIRST SHOWCASE / CURATED CHECK AS OF ${proofs.verified_at}`),
      element("h3", null, proof.title),
      element("p", null, proof.summary),
      badges,
      list,
      element("p", "warning", proof.technocore_evidence.boundary),
      externalLink("Open release ↗", proof.release.url)
    );
    grid.append(card);

    const method = element("article", "proof-card");
    method.append(
      element("span", "card-kicker", "WHAT THIS PROVES"),
      element("h3", null, "Code is durable. Inclusion is not."),
      element("p", null, `As of ${proofs.verified_at}, GitHub exposed the public repository, reviewed commit, and v0.2.0 release. This dated curated check is not continuously re-verified. The legacy room receipt records a same-server read-back claim; it does not cryptographically prove Technocore sequence assignment.`),
      externalLink("Inspect public evidence ↗", proof.technocore_evidence.receipt_url)
    );
    grid.append(method);
  }
}

function drawMap(snapshot, time = 0) {
  const canvas = byId("signal-map");
  const box = canvas.getBoundingClientRect();
  const scale = Math.min(window.devicePixelRatio || 1, 2);
  const width = Math.max(1, Math.floor(box.width));
  const height = Math.max(1, Math.floor(box.height));
  if (canvas.width !== width * scale || canvas.height !== height * scale) {
    canvas.width = width * scale;
    canvas.height = height * scale;
  }
  const context = canvas.getContext("2d");
  context.setTransform(scale, 0, 0, scale, 0, 0);
  context.clearRect(0, 0, width, height);

  context.fillStyle = "rgba(139, 148, 158, 0.16)";
  for (let x = 22; x < width; x += 28) {
    for (let y = 22; y < height; y += 28) context.fillRect(x, y, 1, 1);
  }

  const rooms = snapshot.rooms;
  const centerX = width / 2;
  const centerY = height / 2;
  const radius = Math.min(width, height) * 0.28;
  const nodes = rooms.map((room, index) => {
    const angle = -Math.PI / 2 + (index / Math.max(rooms.length, 1)) * Math.PI * 2;
    return {
      room,
      x: centerX + Math.cos(angle) * radius,
      y: centerY + Math.sin(angle) * radius,
      size: 14 + Math.min(20, Math.sqrt(room.unique_sender_count)),
    };
  });

  context.strokeStyle = "rgba(0, 217, 146, 0.22)";
  context.lineWidth = 1;
  for (let index = 0; index < nodes.length; index += 1) {
    const next = nodes[(index + 1) % nodes.length];
    if (next === nodes[index]) continue;
    context.beginPath();
    context.moveTo(nodes[index].x, nodes[index].y);
    context.lineTo(next.x, next.y);
    context.stroke();
  }

  for (const node of nodes) {
    const coordinationRate = node.room.message_count
      ? node.room.coordination_signal_count / node.room.message_count
      : 0;
    const duplicateRate = node.room.duplicate_rate;
    const pulseAmplitude = 2 + coordinationRate * 10;
    const pulse = reducedMotion.matches
      ? 0
      : Math.sin(time / 900 + node.x) * pulseAmplitude;
    context.beginPath();
    context.arc(
      node.x,
      node.y,
      node.size + 8 + coordinationRate * 12 + pulse,
      0,
      Math.PI * 2,
    );
    context.strokeStyle = `rgba(0, 217, 146, ${0.14 + coordinationRate * 0.4})`;
    context.lineWidth = 1 + coordinationRate * 4;
    context.stroke();
    const satelliteCount = Math.round(duplicateRate * 12);
    context.fillStyle = "rgba(139, 148, 158, 0.62)";
    for (let index = 0; index < satelliteCount; index += 1) {
      const angle = (index / Math.max(satelliteCount, 1)) * Math.PI * 2;
      const distance = node.size + 20 + (index % 2) * 5;
      context.beginPath();
      context.arc(
        node.x + Math.cos(angle) * distance,
        node.y + Math.sin(angle) * distance,
        1.8,
        0,
        Math.PI * 2,
      );
      context.fill();
    }
    context.beginPath();
    context.arc(node.x, node.y, node.size, 0, Math.PI * 2);
    context.fillStyle = "rgba(0, 217, 146, 0.12)";
    context.fill();
    context.strokeStyle = "#00d992";
    context.stroke();
    context.fillStyle = "#f2f2f2";
    context.font = "500 11px SFMono-Regular, Menlo, monospace";
    context.textAlign = "center";
    context.fillText(`#${node.room.room}`, node.x, node.y - node.size - 14);
    context.fillStyle = "#8b949e";
    context.font = "10px SFMono-Regular, Menlo, monospace";
    context.fillText(`${node.room.unique_sender_count} senders`, node.x, node.y + 4);
  }
}

function animateMap(time) {
  animationFrame = null;
  if (!currentSnapshot) return;
  drawMap(currentSnapshot, time);
  if (!reducedMotion.matches) animationFrame = window.requestAnimationFrame(animateMap);
}

function clearDynamic() {
  currentSnapshot = null;
  if (animationFrame !== null) window.cancelAnimationFrame(animationFrame);
  animationFrame = null;
  for (const id of ["metric-messages", "metric-signed", "metric-duplicates", "metric-coordination"]) {
    byId(id).textContent = "—";
  }
  byId("rooms-body").replaceChildren();
  byId("map-values").replaceChildren();
  byId("projects-grid").replaceChildren();
  byId("proofs-grid").replaceChildren();
  byId("generated-at").textContent = "Snapshot time unavailable";
  const canvas = byId("signal-map");
  canvas.getContext("2d").clearRect(0, 0, canvas.width, canvas.height);
}

async function load() {
  try {
    const [snapshotResponse, proofsResponse] = await Promise.all([
      fetch("./data/snapshot.json", { cache: "no-store" }),
      fetch("./data/proofs.json", { cache: "no-store" }),
    ]);
    if (!snapshotResponse.ok || !proofsResponse.ok) throw new Error("snapshot unavailable");
    const snapshot = await snapshotResponse.json();
    const proofs = await proofsResponse.json();
    validateSnapshot(snapshot);
    validateProofs(proofs);
    currentSnapshot = snapshot;
    setMetrics(snapshot);
    setRooms(snapshot);
    setMapValues(snapshot);
    setProjects(snapshot);
    setProofs(proofs);
    byId("data-status").textContent = "SNAPSHOT LOADED";
    byId("data-status").classList.remove("warning");
    byId("generated-at").textContent = `SNAPSHOT / ${snapshot.generated_at}`;
    animateMap(0);
  } catch {
    clearDynamic();
    byId("data-status").textContent = "SNAPSHOT UNAVAILABLE";
    byId("data-status").classList.add("warning");
    byId("snapshot-note").textContent = "The last public snapshot could not be loaded. No live room fallback is attempted in the browser.";
  }
}

window.addEventListener("resize", () => {
  if (currentSnapshot) drawMap(currentSnapshot, 0);
});
reducedMotion.addEventListener("change", () => {
  if (!currentSnapshot) return;
  if (animationFrame !== null) window.cancelAnimationFrame(animationFrame);
  animationFrame = null;
  drawMap(currentSnapshot, 0);
  if (!reducedMotion.matches) animationFrame = window.requestAnimationFrame(animateMap);
});

load();
