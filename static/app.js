// For any wide, horizontally-scrollable table (class "table-scroll"), add a
// thin scrollbar directly above it that mirrors the real one. Without this,
// the only scrollbar sits below the table's last row, which can be far off
// screen for a long table.
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll(".table-scroll").forEach((scrollBox) => {
    const table = scrollBox.querySelector("table");
    if (!table) return;

    const topBar = document.createElement("div");
    topBar.className = "table-scroll-top";
    const spacer = document.createElement("div");
    topBar.appendChild(spacer);
    scrollBox.parentNode.insertBefore(topBar, scrollBox);

    const syncSpacerWidth = () => {
      spacer.style.width = table.offsetWidth + "px";
    };
    syncSpacerWidth();
    window.addEventListener("resize", syncSpacerWidth);

    let syncing = false;
    topBar.addEventListener("scroll", () => {
      if (syncing) return;
      syncing = true;
      scrollBox.scrollLeft = topBar.scrollLeft;
      syncing = false;
    });
    scrollBox.addEventListener("scroll", () => {
      if (syncing) return;
      syncing = true;
      topBar.scrollLeft = scrollBox.scrollLeft;
      syncing = false;
    });
  });

  freezeAllMatrixTables();
  window.addEventListener("resize", freezeAllMatrixTables);

  document.querySelectorAll("[data-expand-all]").forEach((btn) => {
    btn.addEventListener("click", () => setAllDetails(true));
  });
  document.querySelectorAll("[data-collapse-all]").forEach((btn) => {
    btn.addEventListener("click", () => setAllDetails(false));
  });

  document.querySelectorAll('input[name="filing-type"]').forEach((radio) => {
    radio.addEventListener("change", (e) => applyFilingTypeFilter(e.target.value));
  });

  document.querySelectorAll('input[name="insider-filter"]').forEach((radio) => {
    radio.addEventListener("change", (e) => applyInsiderFilter(e.target.value));
  });
});

function setAllDetails(open) {
  document.querySelectorAll("details").forEach((el) => {
    el.open = open;
  });
}

// Ownership Stakes page: toggle between all filings, 13D only (activist -
// may seek to influence or control the company), and 13G only (passive,
// no such intent). Rows are tagged with data-filing-type server-side;
// this just shows/hides rows and a one-line reminder of what the current
// selection means. A fund whose every row gets filtered out collapses
// itself entirely rather than showing an empty, expanded table.
const FILING_TYPE_DESCRIPTIONS = {
  all: "",
  "13D": "13D: the filer may seek to influence or control the company - the classic activist filing.",
  "13G": "13G: the same 5% threshold as 13D, but declares no intent to influence or control - a passive stake.",
};

function applyFilingTypeFilter(type) {
  document.querySelectorAll("tr[data-filing-type]").forEach((row) => {
    row.style.display = type === "all" || row.dataset.filingType === type ? "" : "none";
  });

  document.querySelectorAll("[data-ownership-fund]").forEach((details) => {
    const rows = details.querySelectorAll("tr[data-filing-type]");
    if (rows.length === 0) return; // fund had no filings at all - leave its empty-state message alone

    const visibleCount = Array.from(rows).filter((row) => row.style.display !== "none").length;
    details.style.display = visibleCount > 0 ? "" : "none";

    const countEl = details.querySelector("[data-filing-count-text]");
    if (countEl) {
      const noun = visibleCount === 1 ? "filing" : "filings";
      countEl.textContent = `(${visibleCount} ${noun} since ${details.dataset.since})`;
    }
  });

  const descEl = document.getElementById("filing-type-description");
  if (descEl) descEl.textContent = FILING_TYPE_DESCRIPTIONS[type] || "";
}

// Insider Activity page: toggle between every transaction and just open-
// market buys/sells (codes P and S) - most Form 4s are routine
// compensation/tax events, not a real market decision, so this cuts
// straight to the ones that are. Rows are tagged with data-open-market
// server-side; a company whose every transaction gets filtered out
// collapses itself entirely.
const INSIDER_FILTER_DESCRIPTIONS = {
  all: "",
  "open-market": "Showing only genuine open-market purchases (P) and sales (S) - grants, tax withholding, and option exercises are hidden.",
};

function applyInsiderFilter(mode) {
  document.querySelectorAll("tr[data-open-market]").forEach((row) => {
    const show = mode === "all" || row.dataset.openMarket === "true";
    row.style.display = show ? "" : "none";
  });

  document.querySelectorAll("[data-insider-company]").forEach((details) => {
    const rows = details.querySelectorAll("tr[data-open-market]");
    if (rows.length === 0) return;

    const visibleCount = Array.from(rows).filter((row) => row.style.display !== "none").length;
    details.style.display = visibleCount > 0 ? "" : "none";

    const countEl = details.querySelector("[data-insider-count-text]");
    if (countEl) {
      const noun = visibleCount === 1 ? "transaction" : "transactions";
      countEl.textContent = `(${visibleCount} ${noun}, ${details.dataset.fundSuffix})`;
    }
  });

  const descEl = document.getElementById("insider-filter-description");
  if (descEl) descEl.textContent = INSIDER_FILTER_DESCRIPTIONS[mode] || "";
}

// The Overlap page's matrix (Ticker, Company, # Funds, Weighted Avg %)
// freezes 4 leading columns; Momentum's (Ticker, Company, # Funds) freezes
// 3; Position History's (Ticker, Company only) freezes 2 - all share the
// same underlying pinning logic.
function freezeAllMatrixTables() {
  freezeLeadingColumns("table.matrix:not(.history):not(.momentum)", 4);
  freezeLeadingColumns("table.matrix.momentum", 3);
  freezeLeadingColumns("table.matrix.history", 2);
}

// Pins the first `frozenCount` columns of matching tables in place while
// the rest scrolls horizontally - like Excel's freeze panes. Uses each
// column's actual rendered width (rather than a hardcoded pixel value) so
// it still lines up correctly regardless of content length.
function freezeLeadingColumns(selector, frozenCount) {
  document.querySelectorAll(selector).forEach((table) => {
    const firstRow = table.querySelector("tr");
    if (!firstRow) return;

    const offsets = [];
    let cumulative = 0;
    for (let i = 0; i < frozenCount && i < firstRow.children.length; i++) {
      offsets.push(cumulative);
      cumulative += firstRow.children[i].getBoundingClientRect().width;
    }

    table.querySelectorAll("tr").forEach((row) => {
      for (let i = 0; i < offsets.length && i < row.children.length; i++) {
        const cell = row.children[i];
        cell.classList.add("sticky-col");
        cell.style.left = offsets[i] + "px";
      }
    });
  });
}
