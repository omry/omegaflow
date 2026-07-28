const canvas = document.querySelector("#canvas");
const titleInput = document.querySelector("#artwork-title");
const status = document.querySelector("#status");
const saveButton = document.querySelector("#save-artwork");
const exportButton = document.querySelector("#export-artwork");

let artwork = null;
let drag = null;

function setStatus(message) {
  status.textContent = message;
}

function svgPoint(event) {
  const point = artwork.createSVGPoint();
  point.x = event.clientX;
  point.y = event.clientY;
  return point.matrixTransform(artwork.getScreenCTM().inverse());
}

function translation(element) {
  const transform = element.getAttribute("transform") || "";
  const match = transform.match(/translate\(\s*(-?[\d.]+)[ ,]+(-?[\d.]+)\s*\)/);
  return match ? [Number(match[1]), Number(match[2])] : [0, 0];
}

function beginDrag(event) {
  const target = event.target.closest('[data-draggable="true"]');
  if (!target) {
    return;
  }
  const point = svgPoint(event);
  const [x, y] = translation(target);
  drag = { target, pointerId: event.pointerId, x, y, startX: point.x, startY: point.y };
  target.classList.add("dragging");
  target.setPointerCapture(event.pointerId);
  setStatus(`Moving ${target.id.replaceAll("-", " ")}…`);
}

function moveDrag(event) {
  if (!drag || drag.pointerId !== event.pointerId) {
    return;
  }
  const point = svgPoint(event);
  const x = drag.x + point.x - drag.startX;
  const y = drag.y + point.y - drag.startY;
  drag.target.setAttribute("transform", `translate(${x.toFixed(1)} ${y.toFixed(1)})`);
}

function endDrag(event) {
  if (!drag || drag.pointerId !== event.pointerId) {
    return;
  }
  drag.target.classList.remove("dragging");
  drag.target.releasePointerCapture(event.pointerId);
  drag = null;
  setStatus("Unsaved changes");
}

function updateTitle() {
  const title = titleInput.value.trim() || "Untitled artwork";
  artwork.querySelector("#svg-title").textContent = title;
  artwork.querySelector("#poster-title").textContent = title;
  setStatus("Unsaved changes");
}

function serializeArtwork() {
  return new XMLSerializer().serializeToString(artwork);
}

async function postArtwork(path) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "image/svg+xml" },
    body: serializeArtwork(),
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
}

async function saveArtwork() {
  setStatus("Saving…");
  await postArtwork("/api/artwork");
  setStatus("Artwork saved");
}

async function exportArtwork() {
  setStatus("Exporting…");
  await postArtwork("/api/export");
  setStatus("Exported sunset-beach.svg");
}

async function loadArtwork() {
  const response = await fetch("/api/artwork");
  if (!response.ok) {
    throw new Error(await response.text());
  }
  const documentNode = new DOMParser().parseFromString(
    await response.text(),
    "image/svg+xml",
  );
  artwork = documentNode.documentElement;
  canvas.replaceChildren(artwork);
  titleInput.value = artwork.querySelector("#svg-title").textContent;
  artwork.addEventListener("pointerdown", beginDrag);
  artwork.addEventListener("pointermove", moveDrag);
  artwork.addEventListener("pointerup", endDrag);
  artwork.addEventListener("pointercancel", endDrag);
  setStatus("Ready");
}

titleInput.addEventListener("input", updateTitle);
saveButton.addEventListener("click", () => saveArtwork().catch((error) => setStatus(error.message)));
exportButton.addEventListener("click", () => exportArtwork().catch((error) => setStatus(error.message)));
loadArtwork().catch((error) => setStatus(error.message));
