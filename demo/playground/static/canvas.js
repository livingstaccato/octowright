// Canvas page — reads role from query params, renders a 10x10 grid, claims
// tiles via POST and listens for everyone else's claims via SSE.

(function () {
  const GRID_SIZE = 10;
  const params = new URLSearchParams(location.search);
  const role = params.get("role") || "spectator";
  const colour = params.get("colour") || "#0f62fe";
  const isPlayer = role.startsWith("player");

  document.getElementById("role-label").textContent = role;

  const grid = document.getElementById("grid");
  const counter = document.getElementById("claim-counter");
  const tiles = [];

  function renderGrid() {
    grid.innerHTML = "";
    tiles.length = 0;
    for (let r = 0; r < GRID_SIZE; r++) {
      for (let c = 0; c < GRID_SIZE; c++) {
        const tile = document.createElement("button");
        tile.className = "tile";
        tile.type = "button";
        tile.setAttribute("role", "gridcell");
        tile.setAttribute("data-row", String(r));
        tile.setAttribute("data-col", String(c));
        tile.setAttribute("data-testid", `tile-${r}-${c}`);
        if (isPlayer) {
          tile.addEventListener("click", () => claim(r, c));
        }
        grid.appendChild(tile);
        tiles.push(tile);
      }
    }
  }

  function applyState(canvas) {
    let claimed = 0;
    for (let r = 0; r < GRID_SIZE; r++) {
      for (let c = 0; c < GRID_SIZE; c++) {
        const t = tiles[r * GRID_SIZE + c];
        const v = canvas[r] && canvas[r][c];
        if (v) {
          t.style.background = v;
          t.setAttribute("data-claimed", "true");
          claimed++;
        } else {
          t.style.background = "";
          t.removeAttribute("data-claimed");
        }
      }
    }
    counter.textContent = String(claimed);
  }

  function applyTile(row, col, claimedColour) {
    const t = tiles[row * GRID_SIZE + col];
    if (!t) return;
    t.style.background = claimedColour;
    t.setAttribute("data-claimed", "true");
    counter.textContent = String(
      tiles.filter((x) => x.hasAttribute("data-claimed")).length,
    );
  }

  async function claim(row, col) {
    await fetch("/api/claim", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ row, col, colour, claimed_by: role }),
    });
    await fetch("/api/event", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        source: "shared-canvas",
        kind: "canvas",
        message: `${role} claimed tile ${row},${col}`,
      }),
    });
  }

  function connectSSE() {
    const es = new EventSource("/api/events");
    es.onmessage = (msg) => {
      try {
        const event = JSON.parse(msg.data);
        if (event.event === "snapshot") {
          applyState(event.canvas);
        } else if (event.event === "tile_claimed") {
          applyTile(event.row, event.col, event.colour);
        } else if (event.event === "reset") {
          renderGrid();
          counter.textContent = "0";
        }
      } catch {
        /* ignore malformed frames */
      }
    };
  }

  renderGrid();
  connectSSE();
})();
