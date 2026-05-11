let STATE = {
  works: [],
  works_cols: [],
  rehearsals: [],
  rehearsals_cols: [],
  allocation: [],
  schedule: [],
  timed: []
};

// --- UTILS ---

function el(id) { return document.getElementById(id); }

function scheduleId() {
  return document.body?.dataset?.scheduleId || null;
}

/**
 * Security Fix: Fetches the token from the URL (?token=...) 
 * or a global variable if set in edit.html.
 */
function getEditToken() {
  const params = new URLSearchParams(window.location.search);
  return params.get('token') || window.EDIT_TOKEN || "";
}

function apiBase() {
  const sid = scheduleId();
  const token = getEditToken();
  const base = sid ? `/api/s/${encodeURIComponent(sid)}` : `/api`;
  // We append the token to the base path so all calls are authorized
  return token ? `${base}?token=${token}` : base;
}

async function apiGet(path) {
  const connector = path.includes('?') ? '&' : '?';
  const res = await fetch(`${apiBase()}${path}`, { credentials: "same-origin" });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

async function apiPost(path, bodyObj) {
  const res = await fetch(`${apiBase()}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify(bodyObj || {})
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

// --- ACCORDION TOGGLE ---

function toggleAccordion(headerEl) {
  const content = headerEl.nextElementSibling;
  const icon = headerEl.querySelector(".toggle-icon");
  
  const isCollapsed = headerEl.classList.contains("collapsed");
  
  if (isCollapsed) {
    // Open it
    headerEl.classList.remove("collapsed");
    content.classList.remove("hidden");
  } else {
    // Close it
    headerEl.classList.add("collapsed");
    content.classList.add("hidden");
  }
}

// --- RENDERING ---

function renderTable(containerId, rows, cols, onChange, opts = {}) {
  const container = el(containerId);
  if (!container) return;
  container.innerHTML = "";

  if (!cols || cols.length === 0) {
    container.innerHTML = "<div class='msg'>No columns. Please upload an Excel file.</div>";
    return;
  }

  // First 4 columns are frozen, rest are scrollable
  const frozenCols = cols.slice(0, 4);
  const scrollCols = cols.slice(4);
  
  // Store all columns for reference in event handlers
  const allCols = cols;
  
  // Title is the first scrollable column - it should expand to fill available space
  const isTitleColumn = (colName) => colName === "Title" || colName === "title";

  // Create the wrapper with frozen + scroll sections
  const wrap = document.createElement("div");
  wrap.className = "table-wrap";
  wrap.style.cssText = "display:flex; border:1px solid #d1d5db; background:white; border-radius:4px; overflow:hidden;";

  // ===== FROZEN LEFT SECTION =====
  const frozenDiv = document.createElement("div");
  frozenDiv.className = "table-wrap-frozen";
  frozenDiv.style.cssText = "flex:0 0 auto; overflow:hidden;";

  const frozenTable = document.createElement("table");
  frozenTable.className = "grid-table";
  frozenTable.style.cssText = "border-collapse:collapse; margin:0; width:100%; border-right:1px solid #d1d5db; table-layout:auto;";

  // Frozen header
  const frozenThead = document.createElement("thead");
  const frozenTrh = document.createElement("tr");
  frozenCols.forEach(c => {
    const th = document.createElement("th");
    th.textContent = c;
    th.style.cssText = "min-width:120px; width:120px; max-width:120px; box-sizing:border-box; padding:10px 8px; border:none; border-right:1px solid #e5e7eb; background:#f3f4f6; font-weight:600; text-align:left; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; height:44px; line-height:44px;";
    frozenTrh.appendChild(th);
  });
  frozenThead.appendChild(frozenTrh);
  frozenTable.appendChild(frozenThead);

  // Frozen body
  const frozenTbody = document.createElement("tbody");
  rows.forEach((r, ri) => {
    const tr = document.createElement("tr");
    frozenCols.forEach(c => {
      const td = document.createElement("td");
      const inp = document.createElement("input");
      inp.className = "grid-input";
      
      // Check if this is a Date column in rehearsals table
      const isDateCol = (containerId === "rehearsals-table" && c === "Date");
      const isDayCol = (containerId === "rehearsals-table" && (c === "Day" || c === "Unnamed: 2"));
      
      if (isDateCol) {
        inp.type = "date";
        // Convert from datetime string to date format (YYYY-MM-DD)
        const val = r[c];
        if (val && val !== "NaN" && val !== "None") {
          const dateStr = String(val).trim();
          // Parse various date formats to YYYY-MM-DD
          const dateMatch = dateStr.match(/(\d{4})-(\d{2})-(\d{2})/);
          if (dateMatch) {
            inp.value = `${dateMatch[1]}-${dateMatch[2]}-${dateMatch[3]}`;
          } else {
            inp.value = "";
          }
        } else {
          inp.value = "";
        }
      } else {
        const val = r[c];
        if (val === null || val === undefined || String(val) === "NaN" || String(val) === "None") {
          inp.value = "";
        } else {
          inp.value = String(val).trim();
        }
      }
      
      // Make Day column read-only
      if (isDayCol) {
        inp.readOnly = true;
        inp.style.backgroundColor = "#f9fafb";
        inp.style.cursor = "default";
      }
      
      inp.addEventListener("change", () => {
        if (isDateCol) {
          // Store as datetime string for backend compatibility
          rows[ri][c] = inp.value ? `${inp.value} 00:00:00` : "";
          
          // Auto-populate Day column
          if (inp.value) {
            const date = new Date(inp.value);
            const dayName = date.toLocaleDateString('en-GB', { weekday: 'long' });
            
            // Find and update Day column
            const dayColName = allCols.find(col => col === "Day" || col === "Unnamed: 2");
            if (dayColName) {
              rows[ri][dayColName] = dayName;
              // Re-render to show the updated day
              renderTable(containerId, rows, allCols, onChange, opts);
            }
          }
        } else {
          rows[ri][c] = inp.value;
        }
        if (onChange) onChange();
      });

      td.appendChild(inp);
      td.style.cssText = "min-width:120px; width:120px; max-width:120px; box-sizing:border-box; padding:0; border:none; border-right:1px solid #e5e7eb; border-bottom:1px solid #e5e7eb; height:40px; line-height:40px; overflow:hidden;";
      tr.appendChild(td);
    });
    frozenTbody.appendChild(tr);
  });
  frozenTable.appendChild(frozenTbody);
  frozenDiv.appendChild(frozenTable);
  wrap.appendChild(frozenDiv);

  // ===== SCROLLABLE RIGHT SECTION =====
  const scrollDiv = document.createElement("div");
  scrollDiv.className = "table-wrap-scroll";
  scrollDiv.style.cssText = "flex:1 1 auto; overflow-x:auto; overflow-y:hidden;";

  const scrollTable = document.createElement("table");
  scrollTable.className = "grid-table";
  scrollTable.style.cssText = "border-collapse:collapse; margin:0; width:100%; table-layout:auto;";

  // Scrollable header
  const scrollThead = document.createElement("thead");
  const scrollTrh = document.createElement("tr");
  scrollCols.forEach(c => {
    const th = document.createElement("th");
    th.textContent = c;
    const isTitle = isTitleColumn(c);
    const widthStyle = isTitle ? "width:100%; min-width:200px;" : "width:90px; min-width:90px; max-width:90px;";
    th.style.cssText = `${widthStyle} box-sizing:border-box; padding:10px 8px; border:none; border-right:1px solid #e5e7eb; background:#f3f4f6; font-weight:600; text-align:${isTitle ? "left" : "center"}; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; height:44px; line-height:44px;`;
    scrollTrh.appendChild(th);
  });
  scrollThead.appendChild(scrollTrh);
  scrollTable.appendChild(scrollThead);

  // Scrollable body
  const scrollTbody = document.createElement("tbody");
  rows.forEach((r, ri) => {
    const tr = document.createElement("tr");
    scrollCols.forEach((c, colIdx) => {
      const td = document.createElement("td");
      const inp = document.createElement("input");
      inp.className = "grid-input";
      
      // Check if this is a Date column in rehearsals table
      const isDateCol = (containerId === "rehearsals-table" && c === "Date");
      const isDayCol = (containerId === "rehearsals-table" && (c === "Day" || c === "Unnamed: 2"));
      
      if (isDateCol) {
        inp.type = "date";
        // Convert from datetime string to date format (YYYY-MM-DD)
        const val = r[c];
        if (val && val !== "NaN" && val !== "None") {
          const dateStr = String(val).trim();
          // Parse various date formats to YYYY-MM-DD
          const dateMatch = dateStr.match(/(\d{4})-(\d{2})-(\d{2})/);
          if (dateMatch) {
            inp.value = `${dateMatch[1]}-${dateMatch[2]}-${dateMatch[3]}`;
          } else {
            inp.value = "";
          }
        } else {
          inp.value = "";
        }
      } else {
        const val = r[c];
        if (val === null || val === undefined || String(val) === "NaN" || String(val) === "None") {
          inp.value = "";
        } else {
          inp.value = String(val).trim();
        }
      }
      
      // Make Day column read-only
      if (isDayCol) {
        inp.readOnly = true;
        inp.style.backgroundColor = "#f9fafb";
        inp.style.cursor = "default";
      }
      
      inp.addEventListener("change", () => {
        if (isDateCol) {
          // Store as datetime string for backend compatibility
          rows[ri][c] = inp.value ? `${inp.value} 00:00:00` : "";
          
          // Auto-populate Day column
          if (inp.value) {
            const date = new Date(inp.value);
            const dayName = date.toLocaleDateString('en-GB', { weekday: 'long' });
            
            // Find and update Day column (check all columns, not just frozen/scroll)
            const allCols = [...frozenCols, ...scrollCols];
            const dayColName = allCols.find(col => col === "Day" || col === "Unnamed: 2");
            if (dayColName) {
              rows[ri][dayColName] = dayName;
              // Re-render to show the updated day
              renderTable(containerId, rows, allCols, onChange, opts);
            }
          }
        } else {
          rows[ri][c] = inp.value;
        }
        if (onChange) onChange();
      });

      const isLastCol = colIdx === scrollCols.length - 1;
      const isTitle = isTitleColumn(c);
      const widthStyle = isTitle ? "width:100%; min-width:200px;" : "width:90px; min-width:90px; max-width:90px;";
      td.appendChild(inp);
      td.style.cssText = `${widthStyle} box-sizing:border-box; padding:0; border:none; border-right:${isLastCol ? "none" : "1px solid #e5e7eb"}; border-bottom:1px solid #e5e7eb; height:40px; line-height:40px; overflow:hidden;`;
      tr.appendChild(td);
    });
    scrollTbody.appendChild(tr);
  });
  scrollTable.appendChild(scrollTbody);
  scrollDiv.appendChild(scrollTable);
  wrap.appendChild(scrollDiv);

  container.appendChild(wrap);
}

// Ensure saveInputs handles the token correctly
async function saveInputs(clearComputed = false) {
  try {
    const res = await apiPost("/save_inputs", {
      works: STATE.works,
      rehearsals: STATE.rehearsals,
      works_cols: STATE.works_cols,
      rehearsals_cols: STATE.rehearsals_cols,
      clear_computed: clearComputed
    });
    console.log("Auto-save successful");
  } catch (e) {
    console.error("Save failed:", e);
  }
}

// Simple table renderer for read-only output tables
function renderOutputTable(containerId, rows, cols) {
  const container = el(containerId);
  if (!container) return;
  container.innerHTML = "";

  if (!rows || rows.length === 0) {
    container.innerHTML = "<div class='msg'>No data</div>";
    return;
  }

  if (!cols || cols.length === 0) {
    cols = Object.keys(rows[0] || {});
  }

  console.log(`Rendering ${containerId}: ${rows.length} rows, ${cols.length} cols`, { rows: rows.slice(0, 2), cols });

  // For allocation table: freeze only Title column, scroll rehearsals
  let frozenCols, scrollCols;
  if (containerId === "allocation-table" && cols.includes("Title")) {
    frozenCols = ["Title"];
    scrollCols = cols.filter(c => c !== "Title");
  } else {
    // Default: first 4 columns frozen, rest scrollable
    frozenCols = cols.slice(0, 4);
    scrollCols = cols.slice(4);
  }

  const wrap = document.createElement("div");
  wrap.className = "table-wrap";
  // Make wrap a flex container so frozen and scroll sections are side-by-side
  wrap.style.cssText = "display:flex; border:1px solid #d1d5db; background:white; border-radius:4px; overflow:hidden;";

  // ===== FROZEN LEFT SECTION =====
  const frozenDiv = document.createElement("div");
  frozenDiv.className = "table-wrap-frozen";
  frozenDiv.style.cssText = "flex:0 0 auto; overflow:hidden;";

  const frozenTable = document.createElement("table");
  frozenTable.className = "grid-table";
  frozenTable.style.cssText = "border-collapse:collapse; margin:0; width:100%; border-right:1px solid #d1d5db; border-spacing:0;";

  const frozenThead = document.createElement("thead");
  const frozenTrh = document.createElement("tr");
  frozenTrh.style.cssText = "height:40px;";
  frozenCols.forEach(c => {
    const th = document.createElement("th");
    th.textContent = c;
    // Allocation table: make Title column wider
    const width = containerId === "allocation-table" ? "280px" : "120px";
    th.style.cssText = `min-width:${width}; width:${width}; max-width:${width}; box-sizing:border-box; padding:10px 8px; border:none; border-right:1px solid #e5e7eb; background:#f3f4f6; font-weight:600; text-align:left; display:table-cell; vertical-align:middle; height:40px;`;
    frozenTrh.appendChild(th);
  });
  frozenThead.appendChild(frozenTrh);
  frozenTable.appendChild(frozenThead);

  const frozenTbody = document.createElement("tbody");
  rows.forEach((r, rowIdx) => {
    const tr = document.createElement("tr");
    tr.style.cssText = "height:40px;";
    frozenCols.forEach(c => {
      const td = document.createElement("td");
      const val = r[c];
      if (val === null || val === undefined || String(val) === "NaN") {
        td.textContent = "";
      } else {
        td.textContent = String(val);
      }
      const width = containerId === "allocation-table" ? "280px" : "120px";
      td.style.cssText = `min-width:${width}; width:${width}; max-width:${width}; box-sizing:border-box; padding:10px 8px; border:none; border-right:1px solid #e5e7eb; border-bottom:1px solid #e5e7eb; font-size:14px; display:table-cell; vertical-align:middle;`;
      tr.appendChild(td);
    });
    frozenTbody.appendChild(tr);
  });
  frozenTable.appendChild(frozenTbody);
  frozenDiv.appendChild(frozenTable);
  wrap.appendChild(frozenDiv);

  // ===== SCROLLABLE RIGHT SECTION =====
  const scrollDiv = document.createElement("div");
  scrollDiv.className = "table-wrap-scroll";
  scrollDiv.style.cssText = "flex:1 1 auto; overflow-x:auto; overflow-y:hidden;";

  const scrollTable = document.createElement("table");
  scrollTable.className = "grid-table";
  scrollTable.style.cssText = "border-collapse:collapse; margin:0; border-spacing:0;";

  const scrollThead = document.createElement("thead");
  const scrollTrh = document.createElement("tr");
  scrollTrh.style.cssText = "height:40px;";
  scrollCols.forEach(c => {
    const th = document.createElement("th");
    th.textContent = c;
    th.style.cssText = "min-width:90px; width:90px; max-width:90px; box-sizing:border-box; padding:10px 8px; border:none; border-right:1px solid #e5e7eb; background:#f3f4f6; font-weight:600; text-align:center; display:table-cell; vertical-align:middle; height:40px;";
    scrollTrh.appendChild(th);
  });
  scrollThead.appendChild(scrollTrh);
  scrollTable.appendChild(scrollThead);

  const scrollTbody = document.createElement("tbody");
  rows.forEach((r, rowIdx) => {
    const tr = document.createElement("tr");
    tr.style.cssText = "height:40px;";
    scrollCols.forEach((c, colIdx) => {
      const td = document.createElement("td");
      const val = r[c];
      if (val === null || val === undefined || String(val) === "NaN") {
        td.textContent = "";
      } else {
        td.textContent = String(val);
      }
      const isLastCol = colIdx === scrollCols.length - 1;
      td.style.cssText = `min-width:90px; width:90px; max-width:90px; box-sizing:border-box; padding:10px 8px; border:none; border-right:${isLastCol ? "none" : "1px solid #e5e7eb"}; border-bottom:1px solid #e5e7eb; font-size:14px; text-align:center; display:table-cell; vertical-align:middle;`;
      tr.appendChild(td);
    });
    scrollTbody.appendChild(tr);
  });
  scrollTable.appendChild(scrollTbody);
  scrollDiv.appendChild(scrollTable);
  wrap.appendChild(scrollDiv);

  container.appendChild(wrap);
}

function renderScheduleAccordion(containerId, rows) {
  const container = el(containerId);
  if (!container) return;
  container.innerHTML = "";

  if (!rows || rows.length === 0) {
    container.innerHTML = "<div class='msg'>No data</div>";
    return;
  }

  // Group by Rehearsal
  const byRehearsal = {};
  rows.forEach(row => {
    const rnum = row.Rehearsal || "Unknown";
    if (!byRehearsal[rnum]) byRehearsal[rnum] = [];
    byRehearsal[rnum].push(row);
  });

  // Create accordion
  const accordion = document.createElement("div");
  accordion.style.cssText = "border:1px solid #d1d5db; border-radius:4px; overflow:hidden;";

  Object.keys(byRehearsal).sort((a, b) => parseInt(a) - parseInt(b)).forEach((rnum, idx) => {
    const items = byRehearsal[rnum];
    const cols = Object.keys(items[0] || {});

    // Header (clickable)
    const header = document.createElement("div");
    header.style.cssText = "background:#f3f4f6; padding:12px 16px; border-bottom:1px solid #d1d5db; cursor:pointer; display:flex; justify-content:space-between; align-items:center;";
    
    const title = document.createElement("strong");
    title.textContent = `Rehearsal ${rnum} (${items.length} items)`;
    
    const toggle = document.createElement("span");
    toggle.textContent = "▼";
    toggle.style.cssText = "font-size:12px; transition:transform 0.3s;";
    
    header.appendChild(title);
    header.appendChild(toggle);

    // Content (initially hidden)
    const content = document.createElement("div");
    content.style.cssText = "display:none; padding:12px;";
    
    // Render table for this rehearsal
    const table = document.createElement("table");
    table.className = "grid-table";
    table.style.cssText = "width:100%; border-collapse:collapse;";
    
    const thead = document.createElement("thead");
    const headerRow = document.createElement("tr");
    headerRow.style.cssText = "background:#f9fafb;";
    
    cols.forEach(col => {
      const th = document.createElement("th");
      th.textContent = col;
      th.style.cssText = "border:1px solid #d1d5db; padding:8px; font-weight:600; text-align:left;";
      headerRow.appendChild(th);
    });
    
    thead.appendChild(headerRow);
    table.appendChild(thead);

    const tbody = document.createElement("tbody");
    items.forEach(row => {
      const tr = document.createElement("tr");
      cols.forEach(col => {
        const td = document.createElement("td");
        const val = row[col];
        td.textContent = val === null || val === undefined ? "" : String(val);
        td.style.cssText = "border:1px solid #d1d5db; padding:8px; font-size:14px;";
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    
    table.appendChild(tbody);
    content.appendChild(table);

    // Toggle handler
    let isOpen = idx === 0; // First one open by default
    header.addEventListener("click", () => {
      isOpen = !isOpen;
      content.style.display = isOpen ? "block" : "none";
      toggle.style.transform = isOpen ? "rotate(180deg)" : "rotate(0deg)";
    });

    // Set initial state
    content.style.display = isOpen ? "block" : "none";
    toggle.style.transform = isOpen ? "rotate(180deg)" : "rotate(0deg)";

    // Add to accordion
    const section = document.createElement("div");
    section.appendChild(header);
    section.appendChild(content);
    section.style.cssText = "border-bottom:1px solid #d1d5db;";
    
    accordion.appendChild(section);
  });

  container.appendChild(accordion);
}

function renderTimedAccordion(containerId, rows) {
  const container = el(containerId);
  if (!container) return;
  container.innerHTML = "";

  if (!rows || rows.length === 0) {
    container.innerHTML = "<div class='msg'>No data</div>";
    return;
  }

  // Filter out summary rows (those with NULL or missing Rehearsal number in first meaningful column)
  const timedRows = rows.filter(row => {
    const rnum = row.Rehearsal;
    return rnum !== null && rnum !== undefined && rnum !== "";
  });

  if (timedRows.length === 0) {
    container.innerHTML = "<div class='msg'>No timed schedule data</div>";
    return;
  }

  // Group by Rehearsal
  const byRehearsal = {};
  timedRows.forEach(row => {
    const rnum = row.Rehearsal || "Unknown";
    if (!byRehearsal[rnum]) byRehearsal[rnum] = [];
    byRehearsal[rnum].push(row);
  });

  // Create accordion
  const accordion = document.createElement("div");
  accordion.style.cssText = "border:1px solid #d1d5db; border-radius:4px; overflow:hidden;";

  Object.keys(byRehearsal).sort((a, b) => parseInt(a) - parseInt(b)).forEach((rnum, idx) => {
    const items = byRehearsal[rnum];
    const cols = Object.keys(items[0] || {});

    // Header (clickable)
    const header = document.createElement("div");
    header.style.cssText = "background:#f3f4f6; padding:12px 16px; border-bottom:1px solid #d1d5db; cursor:pointer; display:flex; justify-content:space-between; align-items:center;";
    
    const title = document.createElement("strong");
    title.textContent = `Rehearsal ${rnum} (${items.length} items)`;
    
    const toggle = document.createElement("span");
    toggle.textContent = "▼";
    toggle.style.cssText = "font-size:12px; transition:transform 0.3s;";
    
    header.appendChild(title);
    header.appendChild(toggle);

    // Content (initially hidden)
    const content = document.createElement("div");
    content.style.cssText = "display:none; padding:0;";
    
    // Render table for this rehearsal with frozen Title column
    const contentWrap = document.createElement("div");
    contentWrap.style.cssText = "display:flex; overflow:hidden;";
    
    // Find Title column
    const titleColIdx = cols.indexOf("Title");
    const titleCol = titleColIdx >= 0 ? cols[titleColIdx] : null;
    const otherCols = cols.filter(c => c !== "Title");
    
    // FROZEN TITLE SECTION
    if (titleCol) {
      const frozenDiv = document.createElement("div");
      frozenDiv.style.cssText = "flex:0 0 auto; overflow:hidden;";
      
      const frozenTable = document.createElement("table");
      frozenTable.style.cssText = "border-collapse:collapse; margin:0; width:100%;";
      
      const frozenThead = document.createElement("thead");
      const frozenThr = document.createElement("tr");
      const th = document.createElement("th");
      th.textContent = "Title";
      th.style.cssText = "min-width:280px; width:280px; max-width:280px; box-sizing:border-box; padding:10px 8px; border:none; border-right:1px solid #e5e7eb; border-bottom:1px solid #d1d5db; background:#f3f4f6; font-weight:600; text-align:left;";
      frozenThr.appendChild(th);
      frozenThead.appendChild(frozenThr);
      frozenTable.appendChild(frozenThead);
      
      const frozenTbody = document.createElement("tbody");
      items.forEach(row => {
        const tr = document.createElement("tr");
        const td = document.createElement("td");
        const val = row[titleCol];
        td.textContent = val === null || val === undefined ? "" : String(val);
        td.style.cssText = "min-width:280px; width:280px; max-width:280px; box-sizing:border-box; padding:10px 8px; border:none; border-right:1px solid #e5e7eb; border-bottom:1px solid #e5e7eb; font-size:13px;";
        tr.appendChild(td);
        frozenTbody.appendChild(tr);
      });
      frozenTable.appendChild(frozenTbody);
      frozenDiv.appendChild(frozenTable);
      contentWrap.appendChild(frozenDiv);
    }
    
    // SCROLLABLE SECTION
    const scrollDiv = document.createElement("div");
    scrollDiv.style.cssText = "flex:1 1 auto; overflow-x:auto; overflow-y:hidden;";
    
    const table = document.createElement("table");
    table.className = "grid-table";
    table.style.cssText = "border-collapse:collapse; margin:0;";
    
    const thead = document.createElement("thead");
    const headerRow = document.createElement("tr");
    headerRow.style.cssText = "background:#f9fafb;";
    
    otherCols.forEach((col, idx) => {
      const th = document.createElement("th");
      th.textContent = col;
      const isLastCol = idx === otherCols.length - 1;
      th.style.cssText = `min-width:120px; width:120px; max-width:120px; box-sizing:border-box; padding:10px 8px; border:none; border-right:${isLastCol ? "none" : "1px solid #e5e7eb"}; border-bottom:1px solid #d1d5db; background:#f3f4f6; font-weight:600; text-align:left; white-space:nowrap;`;
      headerRow.appendChild(th);
    });
    
    thead.appendChild(headerRow);
    table.appendChild(thead);

    const tbody = document.createElement("tbody");
    items.forEach(row => {
      const tr = document.createElement("tr");
      otherCols.forEach((col, idx) => {
        const td = document.createElement("td");
        const val = row[col];
        let display = "";
        if (val === null || val === undefined) {
          display = "";
        } else if (col.includes("Time") && typeof val === "string" && val.includes("T")) {
          // Parse ISO datetime and extract just HH:MM
          const dt = new Date(val);
          if (!isNaN(dt.getTime())) {
            const hours = String(dt.getHours()).padStart(2, "0");
            const mins = String(dt.getMinutes()).padStart(2, "0");
            display = `${hours}:${mins}`;
          } else {
            display = String(val);
          }
        } else {
          display = String(val);
        }
        td.textContent = display;
        td.style.cssText = "border:1px solid #d1d5db; padding:8px; font-size:13px;";
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    
    table.appendChild(tbody);
    scrollDiv.appendChild(table);
    contentWrap.appendChild(scrollDiv);
    content.appendChild(contentWrap);

    // Toggle handler
    let isOpen = idx === 0; // First one open by default
    header.addEventListener("click", () => {
      isOpen = !isOpen;
      content.style.display = isOpen ? "block" : "none";
      toggle.style.transform = isOpen ? "rotate(180deg)" : "rotate(0deg)";
    });

    // Set initial state
    content.style.display = isOpen ? "block" : "none";
    toggle.style.transform = isOpen ? "rotate(180deg)" : "rotate(0deg)";

    // Add to accordion
    const section = document.createElement("div");
    section.appendChild(header);
    section.appendChild(content);
    section.style.cssText = "border-bottom:1px solid #d1d5db;";
    
    accordion.appendChild(section);
  });

  container.appendChild(accordion);
}

async function refreshFromServer() {
  const data = await apiGet("/state");
  STATE.works_cols = data.works_cols || [];
  STATE.rehearsals_cols = data.rehearsals_cols || [];
  STATE.works = data.works || [];
  STATE.rehearsals = data.rehearsals || [];
  STATE.allocation = data.allocation || [];
  STATE.schedule = data.schedule || [];
  STATE.timed = data.timed || [];
  
  console.log("STATE after refresh:", {
    works: STATE.works.length,
    rehearsals: STATE.rehearsals.length,
    allocation: STATE.allocation.length,
    schedule: STATE.schedule.length,
    timed: STATE.timed.length
  });
  
  renderTable("works-table", STATE.works, STATE.works_cols, saveInputs);
  renderTable("rehearsals-table", STATE.rehearsals, STATE.rehearsals_cols, saveInputs);
  
  // Render output tables (read-only)
  console.log("Checking allocation:", { has: !!STATE.allocation, length: STATE.allocation?.length, firstRow: STATE.allocation?.[0] });
  
  if (STATE.allocation && STATE.allocation.length > 0) {
    const allocCols = Object.keys(STATE.allocation[0]);
    console.log("Rendering allocation with columns:", allocCols);
    renderOutputTable("allocation-table", STATE.allocation, allocCols);
    // Collapse the allocation accordion after a brief delay
    requestAnimationFrame(() => {
      const allHeaders = document.querySelectorAll('.accordion-header');
      allHeaders.forEach(header => {
        if (header.textContent.trim() === "Allocation") {
          header.classList.add("collapsed");
          if (header.nextElementSibling) {
            header.nextElementSibling.classList.add("hidden");
          }
        }
      });
    });
  } else {
    console.warn("⚠️ No allocation data to render");
  }
  
  if (STATE.schedule && STATE.schedule.length > 0) {
    const schedCols = Object.keys(STATE.schedule[0]);
    renderScheduleAccordion("schedule-table", STATE.schedule);
    // Collapse the schedule accordion after a brief delay
    requestAnimationFrame(() => {
      const allHeaders = document.querySelectorAll('.accordion-header');
      allHeaders.forEach(header => {
        if (header.textContent.trim() === "Schedule") {
          header.classList.add("collapsed");
          if (header.nextElementSibling) {
            header.nextElementSibling.classList.add("hidden");
          }
        }
      });
    });
  }
  
  if (STATE.timed && STATE.timed.length > 0) {
    const timedCols = Object.keys(STATE.timed[0]);
    renderTimedAccordion("timed-table", STATE.timed);
  }

  // Render timeline editor (if element exists)
  if (el("timelineContent")) {
    renderTimelineEditor();
  }
}

function addWorkRow() {
  const row = {};
  STATE.works_cols.forEach(c => row[c] = "");
  STATE.works.push(row);
  renderTable("works-table", STATE.works, STATE.works_cols, saveInputs);
}

function addRehearsalRow() {
  const row = {};
  STATE.rehearsals_cols.forEach(c => row[c] = "");
  STATE.rehearsals.push(row);
  renderTable("rehearsals-table", STATE.rehearsals, STATE.rehearsals_cols, saveInputs);
}

async function runAllocation() {
  const msg = el("importMsg");
  msg.textContent = "Running allocation logic...";
  await saveInputs(true);
  await apiPost("/run_allocation", {});
  await refreshFromServer();
  msg.textContent = "Allocation complete.";
}

async function generateSchedule() {
  const msg = el("importMsg");
  msg.textContent = "Generating schedule...";
  await apiPost("/generate_schedule", {});
  await refreshFromServer();
  msg.textContent = "Schedule generated.";
}

async function uploadXlsx() {
  const input = el("xlsxFile");
  const msg = el("importMsg");
  if (!input?.files?.[0]) return;

  const fd = new FormData();
  fd.append("file", input.files[0]);
  msg.textContent = "Uploading...";

  const res = await fetch(`${apiBase()}/import_xlsx`, {
    method: "POST",
    body: fd,
    credentials: "same-origin"
  });
  const out = await res.json();
  msg.textContent = out.ok ? "Import Success" : (out.error || "Failed");
  await refreshFromServer();
}

function initEditor(initial) {
  STATE = { ...STATE, ...initial };
  renderTable("works-table", STATE.works, STATE.works_cols, saveInputs);
  renderTable("rehearsals-table", STATE.rehearsals, STATE.rehearsals_cols, saveInputs);
  // Only render timeline if the element exists
  if (el("timelineContent")) {
    renderTimelineEditor();
  }
}

// ========================
// Timeline Editor - Grid-based 5-min block system with Push/Compact logic
// ========================

const TIMELINE_CONFIG = {
  pixelsPerMinute: 6,     // 6 pixels per minute (30px per 5-min block)
  blockHeight: 30,        // pixels per 5-minute increment
  G: 5,                   // granularity in minutes
  minDuration: 5,         // minimum work/break duration in minutes
  resizeHandleHeight: 10, // height of resize handles in px
};

let TIMELINE_STATE = {
  draggingItemId: null,
  dragZone: null,          // "move" | "resize-top" | "resize-bottom"
  dragStartY: 0,
  dragStartOffset: 0,
  resizingItemId: null,
  resizeEdge: null,        // "top" | "bottom"
  resizeStartY: 0,
  resizeStartDuration: 0,
  isDragging: false,
  previewValid: false,
  previewTimeline: null,
};

// History management for undo/redo
let TIMELINE_HISTORY = {
  stack: [],
  currentIndex: -1,
  maxSize: 50,
};

// Utility functions
function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

function timeStringToMinutes(timeStr) {
  if (!timeStr || timeStr === "?") return 0;
  const parts = timeStr.split(":").map(Number);
  // Handle HH:MM or HH:MM:SS formats
  if (parts.length < 2 || isNaN(parts[0]) || isNaN(parts[1])) return 0;
  return parts[0] * 60 + parts[1];
}

function minutesToTimeString(minutes) {
  const h = Math.floor(minutes / 60) % 24;
  const m = minutes % 60;
  return String(h).padStart(2, "0") + ":" + String(m).padStart(2, "0");
}

function safe_int(val, def) {
  if (val === null || val === undefined || val === "") return def;
  const n = parseInt(val);
  return isNaN(n) ? def : n;
}

// ========================
// Timeline Core Functions
// ========================

/**
 * Convert timed array for a single rehearsal into timeline items
 */
function timedToTimelineItems(timedArray, rehearsalNum) {
  const items = timedArray
    .filter(w => parseInt(w.Rehearsal) === rehearsalNum)
    .map((work, idx) => ({
      id: work._index !== undefined ? `item-${work._index}` : `item-${idx}`,
      originalIndex: work._index !== undefined ? work._index : idx,
      title: work.Title || "Untitled",
      start_time: work['Time in Rehearsal'] || "14:00:00",
      duration_mins: parseInt(work['Rehearsal Time (minutes)']) || 5,
      is_break: work.Title === "Break",
      rehearsal: rehearsalNum,
    }))
    .sort((a, b) => {
      const aMin = timeStringToMinutes(a.start_time);
      const bMin = timeStringToMinutes(b.start_time);
      return aMin - bMin;
    });
  
  return items;
}

/**
 * Convert timeline items back to timed array entries
 */
function timelineItemsToTimed(items) {
  return items.map(item => ({
    _index: item.originalIndex,
    Title: item.title,
    'Time in Rehearsal': item.start_time,
    'Rehearsal Time (minutes)': item.duration_mins,
    Rehearsal: item.rehearsal,
  }));
}

/**
 * Add minutes to a time string
 */
function addMinutes(timeStr, minutes) {
  const totalMins = timeStringToMinutes(timeStr) + minutes;
  return minutesToTimeString(totalMins);
}

/**
 * Deep copy an array of objects
 */
function deepCopy(obj) {
  return JSON.parse(JSON.stringify(obj));
}

/**
 * Get rehearsal boundaries (start/end times and total duration)
 */
function getRehearsalBounds(rehearsalNum) {
  const reh = STATE.rehearsals.find(r => parseInt(r.Rehearsal) === rehearsalNum);
  if (!reh) return null;
  
  const startTime = reh['Start Time'] || "14:00:00";
  const endTime = reh['End Time'] || "17:00:00";
  const breakMins = parseInt(reh['Break']) || 0;
  
  const startMins = timeStringToMinutes(startTime);
  const endMins = timeStringToMinutes(endTime);
  const totalMins = endMins - startMins;
  const workingMins = totalMins - breakMins;
  
  return {
    startTime,
    endTime,
    startMins,
    endMins,
    totalMins,
    breakMins,
    workingMins,
  };
}

/**
 * Validate timeline items
 */
function validateTimeline(items, rehearsalNum) {
  const errors = [];
  const bounds = getRehearsalBounds(rehearsalNum);
  
  if (!bounds) {
    return { valid: false, errors: ['Rehearsal not found'] };
  }
  
  // Check minimum durations
  items.forEach(item => {
    if (item.duration_mins < TIMELINE_CONFIG.minDuration) {
      errors.push(`${item.title} is less than minimum ${TIMELINE_CONFIG.minDuration} minutes`);
    }
  });
  
  // Check continuity (no gaps)
  for (let i = 0; i < items.length - 1; i++) {
    const currentEnd = addMinutes(items[i].start_time, items[i].duration_mins);
    const nextStart = items[i + 1].start_time;
    if (currentEnd !== nextStart) {
      errors.push(`Gap between ${items[i].title} and ${items[i + 1].title}`);
    }
  }
  
  // Check bounds
  if (items.length > 0) {
    const firstStart = items[0].start_time;
    const lastItem = items[items.length - 1];
    const lastEnd = addMinutes(lastItem.start_time, lastItem.duration_mins);
    
    if (firstStart !== bounds.startTime) {
      errors.push(`Timeline doesn't start at rehearsal start (${bounds.startTime})`);
    }
    
    if (lastEnd !== bounds.endTime) {
      errors.push(`Timeline doesn't end at rehearsal end (${bounds.endTime}). Got ${lastEnd}, expected ${bounds.endTime}`);
    }
  }
  
  // Check total duration doesn't exceed rehearsal time
  const totalDuration = items.reduce((sum, item) => sum + item.duration_mins, 0);
  if (totalDuration > bounds.totalMins) {
    errors.push(`Total duration (${totalDuration}min) exceeds rehearsal time (${bounds.totalMins}min)`);
  }
  
  return {
    valid: errors.length === 0,
    errors,
  };
}

/**
 * Compact timeline - remove gaps and recalculate start times
 */
function compactTimeline(items, rehearsalNum) {
  const bounds = getRehearsalBounds(rehearsalNum);
  if (!bounds || items.length === 0) return items;
  
  let currentTime = bounds.startTime;
  
  return items.map(item => ({
    ...item,
    start_time: currentTime,
    start_time: (() => {
      const start = currentTime;
      currentTime = addMinutes(currentTime, item.duration_mins);
      return start;
    })(),
  }));
}

/**
 * Push operation - move item to new position and compact
 */
function calculatePushResult(draggedItem, targetTime, timeline, rehearsalNum) {
  // Remove dragged item
  const otherItems = timeline.filter(item => item.id !== draggedItem.id);
  
  // Find insertion index based on target time
  let insertIndex = 0;
  const targetMins = timeStringToMinutes(targetTime);
  
  for (let i = 0; i < otherItems.length; i++) {
    const itemMins = timeStringToMinutes(otherItems[i].start_time);
    if (itemMins < targetMins) {
      insertIndex = i + 1;
    } else {
      break;
    }
  }
  
  // Insert at new position
  const newOrder = [
    ...otherItems.slice(0, insertIndex),
    draggedItem,
    ...otherItems.slice(insertIndex),
  ];
  
  // Compact - recalculate all start times
  const compacted = compactTimeline(newOrder, rehearsalNum);
  
  // Validate
  const validation = validateTimeline(compacted, rehearsalNum);
  
  return {
    timeline: compacted,
    valid: validation.valid,
    errors: validation.errors,
  };
}

/**
 * Resize operation - expand or shrink item and adjust neighbors
 */
function calculateResizeResult(resizedItem, newDuration, edge, timeline, rehearsalNum) {
  const itemIndex = timeline.findIndex(i => i.id === resizedItem.id);
  if (itemIndex === -1) return { valid: false, errors: ['Item not found'] };
  
  // Validate minimum duration
  if (newDuration < TIMELINE_CONFIG.minDuration) {
    return {
      valid: false,
      errors: [`Minimum duration is ${TIMELINE_CONFIG.minDuration} minutes`],
    };
  }
  
  const newTimeline = deepCopy(timeline);
  const deltaMinutes = newDuration - resizedItem.duration_mins;
  
  if (deltaMinutes === 0) {
    return { timeline: newTimeline, valid: true, errors: [] };
  }
  
  // === EXPANDING ===
  if (deltaMinutes > 0) {
    let remainingDelta = deltaMinutes;
    
    if (edge === 'bottom') {
      // Expand at bottom - shrink items after
      let affectedIndex = itemIndex + 1;
      
      while (remainingDelta > 0 && affectedIndex < newTimeline.length) {
        const affectedItem = newTimeline[affectedIndex];
        const availableShrink = affectedItem.duration_mins - TIMELINE_CONFIG.minDuration;
        
        if (availableShrink > 0) {
          const shrinkBy = Math.min(availableShrink, remainingDelta);
          affectedItem.duration_mins -= shrinkBy;
          remainingDelta -= shrinkBy;
        }
        
        affectedIndex++;
      }
      
      if (remainingDelta > 0) {
        return {
          valid: false,
          errors: ['Cannot shrink enough following items'],
        };
      }
      
      newTimeline[itemIndex].duration_mins = newDuration;
    } else {
      // Expand at top - shrink items before
      let affectedIndex = itemIndex - 1;
      
      while (remainingDelta > 0 && affectedIndex >= 0) {
        const affectedItem = newTimeline[affectedIndex];
        const availableShrink = affectedItem.duration_mins - TIMELINE_CONFIG.minDuration;
        
        if (availableShrink > 0) {
          const shrinkBy = Math.min(availableShrink, remainingDelta);
          affectedItem.duration_mins -= shrinkBy;
          remainingDelta -= shrinkBy;
        }
        
        affectedIndex--;
      }
      
      if (remainingDelta > 0) {
        return {
          valid: false,
          errors: ['Cannot shrink enough previous items'],
        };
      }
      
      newTimeline[itemIndex].duration_mins = newDuration;
    }
  }
  // === SHRINKING ===
  else {
    const expandMinutes = Math.abs(deltaMinutes);
    
    if (edge === 'bottom') {
      // Shrink at bottom - expand next item to fill gap
      if (itemIndex + 1 < newTimeline.length) {
        newTimeline[itemIndex + 1].duration_mins += expandMinutes;
      } else if (itemIndex > 0) {
        // Last item - expand previous
        newTimeline[itemIndex - 1].duration_mins += expandMinutes;
      } else {
        return { valid: false, errors: ['Cannot shrink only item'] };
      }
      
      newTimeline[itemIndex].duration_mins = newDuration;
    } else {
      // Shrink at top - expand previous item
      if (itemIndex > 0) {
        newTimeline[itemIndex - 1].duration_mins += expandMinutes;
      } else if (itemIndex + 1 < newTimeline.length) {
        // First item - expand next
        newTimeline[itemIndex + 1].duration_mins += expandMinutes;
      } else {
        return { valid: false, errors: ['Cannot shrink only item'] };
      }
      
      newTimeline[itemIndex].duration_mins = newDuration;
    }
  }
  
  // Compact and validate
  const compacted = compactTimeline(newTimeline, rehearsalNum);
  const validation = validateTimeline(compacted, rehearsalNum);
  
  return {
    timeline: compacted,
    valid: validation.valid,
    errors: validation.errors,
  };
}

// ========================
// History Functions
// ========================

function pushToHistory(operation, beforeState, afterState) {
  // Remove any "future" history if we're in middle of stack
  if (TIMELINE_HISTORY.currentIndex < TIMELINE_HISTORY.stack.length - 1) {
    TIMELINE_HISTORY.stack = TIMELINE_HISTORY.stack.slice(0, TIMELINE_HISTORY.currentIndex + 1);
  }
  
  TIMELINE_HISTORY.stack.push({
    timestamp: Date.now(),
    operation: operation,
    before: deepCopy(beforeState),
    after: deepCopy(afterState),
  });
  
  // Limit history size
  if (TIMELINE_HISTORY.stack.length > TIMELINE_HISTORY.maxSize) {
    TIMELINE_HISTORY.stack.shift();
  } else {
    TIMELINE_HISTORY.currentIndex++;
  }
  
  updateHistoryButtons();
}

function timelineUndo() {
  if (TIMELINE_HISTORY.currentIndex < 0) return;
  
  const entry = TIMELINE_HISTORY.stack[TIMELINE_HISTORY.currentIndex];
  applyTimelineState(entry.before);
  TIMELINE_HISTORY.currentIndex--;
  
  updateHistoryButtons();
  saveTimelineToBackend();
}

function timelineRedo() {
  if (TIMELINE_HISTORY.currentIndex >= TIMELINE_HISTORY.stack.length - 1) return;
  
  TIMELINE_HISTORY.currentIndex++;
  const entry = TIMELINE_HISTORY.stack[TIMELINE_HISTORY.currentIndex];
  applyTimelineState(entry.after);
  
  updateHistoryButtons();
  saveTimelineToBackend();
}

function revertToOriginal() {
  if (!confirm("Revert all timeline changes? This cannot be undone.")) return;
  
  // Load original from first history entry or regenerate
  if (TIMELINE_HISTORY.stack.length > 0 && TIMELINE_HISTORY.stack[0].before) {
    applyTimelineState(TIMELINE_HISTORY.stack[0].before);
  }
  
  // Clear history
  TIMELINE_HISTORY.stack = [];
  TIMELINE_HISTORY.currentIndex = -1;
  
  updateHistoryButtons();
  saveTimelineToBackend();
}

function applyTimelineState(timedArray) {
  STATE.timed = deepCopy(timedArray);
  renderTimelineEditor();
}

function updateHistoryButtons() {
  const undoBtn = document.getElementById('timelineUndoBtn');
  const redoBtn = document.getElementById('timelineRedoBtn');
  const revertBtn = document.getElementById('timelineRevertBtn');
  
  if (undoBtn) undoBtn.disabled = (TIMELINE_HISTORY.currentIndex < 0);
  if (redoBtn) redoBtn.disabled = (TIMELINE_HISTORY.currentIndex >= TIMELINE_HISTORY.stack.length - 1);
  if (revertBtn) revertBtn.disabled = (TIMELINE_HISTORY.stack.length === 0);
}

async function saveTimelineToBackend() {
  try {
    await apiPost("/save_timed", { timed: STATE.timed });
    console.log("Timeline auto-saved");
  } catch (e) {
    console.error("Timeline save failed:", e);
  }
}

// Helper: Calculate work duration by looking at gap to next work
function calculateWorkDuration(work, workIdx, allWorks) {
  // First, try to use stored duration
  let dur = safe_int(work['Rehearsal Time (minutes)'], null);
  if (dur && dur > 0) return dur;
  
  // If no stored duration, calculate from gap to next work
  const currentTime = timeStringToMinutes(work['Time in Rehearsal']) || 0;
  
  // Find next non-break work after this one
  for (let i = workIdx + 1; i < allWorks.length; i++) {
    const nextWork = allWorks[i];
    if (nextWork.Title === "Break") continue;
    
    const nextTime = timeStringToMinutes(nextWork['Time in Rehearsal']) || 0;
    if (nextTime > currentTime) {
      const gap = nextTime - currentTime;
      // Round to nearest 5-minute block
      const roundedDur = Math.round(gap / TIMELINE_CONFIG.G) * TIMELINE_CONFIG.G;
      return Math.max(5, roundedDur);
    }
  }
  
  // No next work found, default to 5 minutes
  return 5;
}

// Helper: Cascade blocks when one moves - rebuild entire rehearsal timeline sequentially
function cascadeBlocksAfterMove(timedArray, movedWorkIndex, newStartTime) {
  const movedWork = timedArray[movedWorkIndex];
  const movedRehearsal = movedWork.Rehearsal;
  const newStartMin = timeStringToMinutes(newStartTime);
  
  // Get all works in same rehearsal (excluding breaks)
  const rehearsalIndices = timedArray
    .map((w, idx) => ({ work: w, idx }))
    .filter(({ work }) => work.Rehearsal === movedRehearsal && work.Title !== "Break")
    .sort((a, b) => {
      // Sort by current time to determine order
      const timeA = timeStringToMinutes(a.work['Time in Rehearsal']) || 0;
      const timeB = timeStringToMinutes(b.work['Time in Rehearsal']) || 0;
      return timeA - timeB;
    });
  
  // Build a map of original positions
  const originalOrder = rehearsalIndices.map(item => item.idx);
  
  // Reorder: put the moved work at its new position, keep others in relative order
  const newOrder = originalOrder.filter(idx => idx !== movedWorkIndex);
  
  // Find where to insert the moved work in the new order based on newStartMin
  let insertPos = 0;
  for (let i = 0; i < newOrder.length; i++) {
    const work = timedArray[newOrder[i]];
    const workTime = timeStringToMinutes(work['Time in Rehearsal']) || 0;
    if (workTime < newStartMin) {
      insertPos = i + 1;
    }
  }
  
  // Insert moved work at the correct position
  newOrder.splice(insertPos, 0, movedWorkIndex);
  
  // Now place all blocks sequentially starting from the earliest rehearsal start time
  // Get the rehearsal start time (first work's time)
  let currentTime = newStartMin;
  if (newOrder.length > 0) {
    const firstWork = timedArray[newOrder[0]];
    const firstTime = timeStringToMinutes(firstWork['Time in Rehearsal']) || 0;
    
    // If moved work is first, start from its new time
    if (newOrder[0] === movedWorkIndex) {
      currentTime = newStartMin;
    } else {
      // Otherwise, find the actual start time (should be before moved work)
      currentTime = newStartMin;
      for (const idx of newOrder) {
        if (idx !== movedWorkIndex) {
          const t = timeStringToMinutes(timedArray[idx]['Time in Rehearsal']) || 0;
          currentTime = Math.min(currentTime, t);
        }
      }
    }
  }
  
  // Place blocks sequentially
  for (const idx of newOrder) {
    const work = timedArray[idx];
    work['Time in Rehearsal'] = minutesToTimeString(currentTime);
    
    // Calculate duration for this work
    const workIdx = newOrder.indexOf(idx);
    const rehearsalWorks = newOrder.map(i => timedArray[i]);
    const duration = calculateWorkDuration(work, workIdx, rehearsalWorks);
    
    work['Rehearsal Time (minutes)'] = duration;
    currentTime += duration;
  }
}


function durationToBlocks(minutes) {
  return Math.max(1, Math.round(minutes / TIMELINE_CONFIG.G));
}

// Validation: Check for overlapping blocks within a rehearsal
function validateNoOverlaps(works, rehearsalNum) {
  const filtered = works.filter(w => w.Title !== "Break");
  for (let i = 0; i < filtered.length; i++) {
    for (let j = i + 1; j < filtered.length; j++) {
      const w1 = filtered[i];
      const w2 = filtered[j];
      const start1 = timeStringToMinutes(w1['Time in Rehearsal']);
      const dur1 = safe_int(w1['Rehearsal Time (minutes)'], 5);
      const end1 = start1 + dur1;
      const start2 = timeStringToMinutes(w2['Time in Rehearsal']);
      const dur2 = safe_int(w2['Rehearsal Time (minutes)'], 5);
      const end2 = start2 + dur2;
      
      if ((start1 < end2 && start2 < end1)) {
        return { valid: false, error: `"${w1.Title}" and "${w2.Title}" overlap` };
      }
    }
  }
  return { valid: true };
}

// Validation: Check that all blocks have valid start and end times
function validateBlockTimes(works) {
  for (const work of works) {
    if (work.Title === "Break") continue;
    const startTime = work['Time in Rehearsal'];
    const duration = safe_int(work['Rehearsal Time (minutes)'], 5);
    
    if (!startTime || isNaN(timeStringToMinutes(startTime)) || duration <= 0) {
      return { valid: false, error: `"${work.Title}" has invalid time or duration` };
    }
  }
  return { valid: true };
}

// Save timeline with validation
async function saveTimeline() {
  try {
    // Validate all rehearsals
    const byRehearsal = {};
    STATE.timed.forEach((row, idx) => {
      const rnum = row.Rehearsal;
      if (!byRehearsal[rnum]) byRehearsal[rnum] = [];
      byRehearsal[rnum].push(row);
    });
    
    // Check each rehearsal for issues
    for (const rnum in byRehearsal) {
      const check = validateBlockTimes(byRehearsal[rnum]);
      if (!check.valid) {
        console.warn(`Validation warning for Rehearsal ${rnum}: ${check.error}`);
      }
      const overlapCheck = validateNoOverlaps(byRehearsal[rnum], rnum);
      if (!overlapCheck.valid) {
        console.warn(`Overlap warning for Rehearsal ${rnum}: ${overlapCheck.error}`);
      }
    }
    
    // Save to server
    const res = await fetch(`${apiBase()}/save_timed`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(STATE.timed),
      credentials: "same-origin"
    });
    
    if (!res.ok) {
      console.error("Save failed:", await res.text());
      return false;
    }
    
    console.log("Timeline saved successfully");
    return true;
  } catch (err) {
    console.error("Save error:", err);
    return false;
  }
}

function renderTimelineEditor() {
  const contentEl = document.getElementById("timelineContent");
  if (!contentEl) {
    console.warn("Timeline element #timelineContent not found");
    return;
  }

  // Add undo/redo/revert buttons if not already present
  if (!document.getElementById("timelineUndoBtn")) {
    const controls = document.createElement("div");
    controls.className = "timeline-controls";
    controls.style.cssText = "display:flex; gap:8px; margin-bottom:12px;";
    controls.innerHTML = `
      <button id="timelineUndoBtn" onclick="timelineUndo()" disabled class="btn secondary">
        ↶ Undo
      </button>
      <button id="timelineRedoBtn" onclick="timelineRedo()" disabled class="btn secondary">
        ↷ Redo
      </button>
      <button id="timelineRevertBtn" onclick="revertToOriginal()" disabled class="btn secondary" style="margin-left:auto;">
        ⟲ Revert to Original
      </button>
    `;
    contentEl.parentElement.insertBefore(controls, contentEl);
  }

  try {
    if (!STATE.timed || STATE.timed.length === 0) {
      contentEl.innerHTML = '<div style="padding:20px; color:#999;">No timed schedule yet. Run allocation and generate schedule first.</div>';
      return;
    }

    // Group by rehearsal
    const byRehearsal = {};
    STATE.timed.forEach((row, idx) => {
      const rnum = parseInt(row.Rehearsal);
      if (!byRehearsal[rnum]) byRehearsal[rnum] = [];
      byRehearsal[rnum].push({ ...row, _index: idx });
    });

    // Create grid container
    const gridDiv = document.createElement("div");
    gridDiv.className = "timeline-grid";
    gridDiv.style.cssText = "display:flex; gap:20px; overflow-x:auto; padding:12px; background:#fafafa; border:1px solid #e5e7eb; border-radius:4px;";

    // Render each rehearsal
    Object.keys(byRehearsal)
      .sort((a, b) => parseInt(a) - parseInt(b))
      .forEach(rnum => {
        const column = renderRehearsalColumn(parseInt(rnum), byRehearsal[rnum]);
        gridDiv.appendChild(column);
      });

    contentEl.innerHTML = "";
    contentEl.appendChild(gridDiv);

    // Update history buttons
    updateHistoryButtons();

  } catch (err) {
    console.error("Error rendering timeline:", err);
    contentEl.innerHTML = '<div style="padding:20px; color:red;">Error: ' + escapeHtml(err.message) + '</div>';
  }
}

/**
 * Render a single rehearsal column
 */
function renderRehearsalColumn(rehearsalNum, works) {
  const bounds = getRehearsalBounds(rehearsalNum);
  if (!bounds) {
    const errDiv = document.createElement("div");
    errDiv.textContent = `Rehearsal ${rehearsalNum} not found`;
    return errDiv;
  }

  // Convert to timeline items
  const items = timedToTimelineItems(STATE.timed, rehearsalNum);

  // Column container
  const column = document.createElement("div");
  column.style.cssText = "flex: 0 0 350px; border:1px solid #d1d5db; border-radius:4px; overflow:hidden; background:white; display:flex; flex-direction:column;";

  // Header
  const header = document.createElement("div");
  header.style.cssText = "padding:12px; background:#f3f4f6; border-bottom:1px solid #d1d5db; font-weight:600; text-align:center;";
  header.textContent = `Rehearsal ${rehearsalNum}`;
  column.appendChild(header);

  // Timeline container (scrollable)
  const scrollContainer = document.createElement("div");
  scrollContainer.style.cssText = "flex: 1; overflow-y:auto; overflow-x:hidden; position:relative;";

  // Calculate height based on rehearsal duration
  const durationMins = bounds.totalMins;
  const containerHeight = durationMins * TIMELINE_CONFIG.pixelsPerMinute + 40;

  // Timeline content (with grid lines and labels)
  const timelineContent = document.createElement("div");
  timelineContent.style.cssText = `position:relative; width:100%; height:${containerHeight}px; background:white; padding-left:60px;`;
  timelineContent.dataset.rehearsalNum = rehearsalNum;

  // Add grid lines and time labels
  const numLines = Math.ceil(durationMins / TIMELINE_CONFIG.G) + 1;
  for (let i = 0; i <= numLines; i++) {
    const mins = i * TIMELINE_CONFIG.G;
    const y = mins * TIMELINE_CONFIG.pixelsPerMinute;
    
    // Grid line
    const line = document.createElement("div");
    line.style.cssText = `position:absolute; left:0; right:0; top:${y}px; height:1px; background:${i === 0 ? '#999' : '#e5e7eb'}; z-index:0;`;
    timelineContent.appendChild(line);

    // Time label (every 10 minutes)
    if (i % 2 === 0) {
      const label = document.createElement("div");
      const absTime = addMinutes(bounds.startTime, mins);
      label.textContent = absTime.substring(0, 5); // HH:MM
      label.style.cssText = `position:absolute; left:5px; top:${y - 10}px; font-size:11px; color:#666; width:50px; text-align:right;`;
      timelineContent.appendChild(label);
    }
  }

  // Render timeline items
  items.forEach((item, idx) => {
    const itemElement = renderTimelineItem(item, rehearsalNum, bounds);
    timelineContent.appendChild(itemElement);
  });

  scrollContainer.appendChild(timelineContent);
  column.appendChild(scrollContainer);

  return column;
}

/**
 * Render a single timeline item (work or break)
 */
function renderTimelineItem(item, rehearsalNum, bounds) {
  const startMins = timeStringToMinutes(item.start_time) - bounds.startMins;
  const topPx = startMins * TIMELINE_CONFIG.pixelsPerMinute;
  const heightPx = item.duration_mins * TIMELINE_CONFIG.pixelsPerMinute;

  const endTime = addMinutes(item.start_time, item.duration_mins);

  // Main item container
  const itemEl = document.createElement("div");
  itemEl.className = "timeline-item";
  itemEl.id = item.id;
  itemEl.dataset.itemId = item.id;
  itemEl.dataset.rehearsalNum = rehearsalNum;
  itemEl.dataset.originalIndex = item.originalIndex;

  const bgColor = item.is_break ? '#f59e0b' : '#3b82f6';
  const borderColor = item.is_break ? '#d97706' : '#2563eb';

  itemEl.style.cssText = `
    position:absolute;
    left:0;
    right:0;
    top:${topPx}px;
    height:${heightPx}px;
    background:${bgColor};
    border:1px solid ${borderColor};
    border-radius:4px;
    color:white;
    cursor:grab;
    user-select:none;
    z-index:10;
    margin:2px;
    display:flex;
    flex-direction:column;
    transition: filter 0.2s, opacity 0.2s;
  `;

  // Top resize handle
  const resizeTop = document.createElement("div");
  resizeTop.className = "resize-handle resize-top";
  resizeTop.style.cssText = `
    position:absolute;
    top:0;
    left:0;
    right:0;
    height:${TIMELINE_CONFIG.resizeHandleHeight}px;
    background:rgba(255,255,255,0.2);
    cursor:ns-resize;
    border-radius:4px 4px 0 0;
    z-index:20;
  `;
  resizeTop.addEventListener("mouseenter", () => resizeTop.style.background = "rgba(255,255,255,0.4)");
  resizeTop.addEventListener("mouseleave", () => resizeTop.style.background = "rgba(255,255,255,0.2)");
  itemEl.appendChild(resizeTop);

  // Content (middle - move zone)
  const content = document.createElement("div");
  content.className = "item-content";
  content.style.cssText = `
    flex:1;
    padding:8px 12px;
    overflow:hidden;
    display:flex;
    flex-direction:column;
    justify-content:center;
    cursor:grab;
  `;
  content.innerHTML = `
    <div style="font-weight:600; font-size:12px; margin-bottom:4px;">${escapeHtml(item.title)}</div>
    <div style="font-size:11px; opacity:0.9;">${item.start_time.substring(0,5)}–${endTime.substring(0,5)}</div>
    <div style="font-size:10px; opacity:0.8;">${item.duration_mins} min</div>
  `;
  itemEl.appendChild(content);

  // Bottom resize handle
  const resizeBottom = document.createElement("div");
  resizeBottom.className = "resize-handle resize-bottom";
  resizeBottom.style.cssText = `
    position:absolute;
    bottom:0;
    left:0;
    right:0;
    height:${TIMELINE_CONFIG.resizeHandleHeight}px;
    background:rgba(255,255,255,0.2);
    cursor:ns-resize;
    border-radius:0 0 4px 4px;
    z-index:20;
  `;
  resizeBottom.addEventListener("mouseenter", () => resizeBottom.style.background = "rgba(255,255,255,0.4)");
  resizeBottom.addEventListener("mouseleave", () => resizeBottom.style.background = "rgba(255,255,255,0.2)");
  itemEl.appendChild(resizeBottom);

  // Hover effect on content
  content.addEventListener("mouseenter", () => itemEl.style.filter = "brightness(1.15)");
  content.addEventListener("mouseleave", () => {
    if (!TIMELINE_STATE.isDragging) itemEl.style.filter = "brightness(1)";
  });

  // Attach event handlers
  attachItemEventHandlers(itemEl, item, rehearsalNum, content, resizeTop, resizeBottom);

  return itemEl;
}

/**
 * Attach drag and resize event handlers to timeline item
 */
function attachItemEventHandlers(itemEl, item, rehearsalNum, content, resizeTop, resizeBottom) {
  let startY = 0;
  let startTop = 0;
  let startDuration = 0;

  // MOVE: MouseDown on content
  const onMoveStart = (e) => {
    e.preventDefault();
    e.stopPropagation();

    TIMELINE_STATE.isDragging = true;
    TIMELINE_STATE.draggingItemId = item.id;
    TIMELINE_STATE.dragZone = "move";
    TIMELINE_STATE.dragStartY = e.clientY;

    startY = e.clientY;
    startTop = parseFloat(itemEl.style.top);

    itemEl.style.opacity = "0.7";
    itemEl.style.cursor = "grabbing";
    content.style.cursor = "grabbing";
    itemEl.style.zIndex = "1000";

    // Store original state for history
    if (TIMELINE_HISTORY.stack.length === 0 || TIMELINE_HISTORY.currentIndex === -1) {
      // First edit - save original state
      const originalState = deepCopy(STATE.timed);
      TIMELINE_HISTORY.stack.push({
        timestamp: Date.now(),
        operation: "initial",
        before: originalState,
        after: originalState,
      });
      TIMELINE_HISTORY.currentIndex = 0;
    }

    document.addEventListener("mousemove", onMoveMove);
    document.addEventListener("mouseup", onMoveEnd);
  };

  const onMoveMove = (e) => {
    const deltaY = e.clientY - startY;
    const newTop = Math.max(0, startTop + deltaY);

    // Snap to 5-minute increments
    const snappedTop = Math.round(newTop / (TIMELINE_CONFIG.G * TIMELINE_CONFIG.pixelsPerMinute)) * (TIMELINE_CONFIG.G * TIMELINE_CONFIG.pixelsPerMinute);

    itemEl.style.top = snappedTop + "px";

    // Calculate target time
    const bounds = getRehearsalBounds(rehearsalNum);
    const offsetMins = snappedTop / TIMELINE_CONFIG.pixelsPerMinute;
    const targetTime = addMinutes(bounds.startTime, offsetMins);

    // Preview the move
    const timeline = timedToTimelineItems(STATE.timed, rehearsalNum);
    const draggedItem = timeline.find(i => i.id === item.id);

    if (draggedItem) {
      const result = calculatePushResult(draggedItem, targetTime, timeline, rehearsalNum);

      if (result.valid) {
        itemEl.style.border = "2px solid #22c55e";
        TIMELINE_STATE.previewValid = true;
        TIMELINE_STATE.previewTimeline = result.timeline;
      } else {
        itemEl.style.border = "2px solid #ef4444";
        TIMELINE_STATE.previewValid = false;
        console.log("Invalid move:", result.errors);
      }
    }
  };

  const onMoveEnd = (e) => {
    document.removeEventListener("mousemove", onMoveMove);
    document.removeEventListener("mouseup", onMoveEnd);

    itemEl.style.opacity = "1";
    itemEl.style.cursor = "grab";
    content.style.cursor = "grab";
    itemEl.style.zIndex = "10";
    itemEl.style.border = "";

    if (TIMELINE_STATE.previewValid && TIMELINE_STATE.previewTimeline) {
      // Apply the move
      const beforeState = deepCopy(STATE.timed);

      // Update STATE.timed with new timeline
      const updatedItems = timelineItemsToTimed(TIMELINE_STATE.previewTimeline);
      updatedItems.forEach(updated => {
        const idx = STATE.timed.findIndex(t => t._index === updated._index);
        if (idx !== -1) {
          STATE.timed[idx]['Time in Rehearsal'] = updated['Time in Rehearsal'];
          STATE.timed[idx]['Rehearsal Time (minutes)'] = updated['Rehearsal Time (minutes)'];
        }
      });

      const afterState = deepCopy(STATE.timed);

      // Push to history
      pushToHistory("move", beforeState, afterState);

      // Save and re-render
      saveTimelineToBackend();
      renderTimelineEditor();
    } else {
      // Snap back to original position
      itemEl.style.top = startTop + "px";
      itemEl.style.animation = "shake 0.3s";
      setTimeout(() => itemEl.style.animation = "", 300);
    }

    TIMELINE_STATE.isDragging = false;
    TIMELINE_STATE.draggingItemId = null;
    TIMELINE_STATE.previewValid = false;
    TIMELINE_STATE.previewTimeline = null;
  };

  content.addEventListener("mousedown", onMoveStart);

  // RESIZE: MouseDown on handles
  const onResizeStart = (edge) => (e) => {
    e.preventDefault();
    e.stopPropagation();

    TIMELINE_STATE.isDragging = true;
    TIMELINE_STATE.resizingItemId = item.id;
    TIMELINE_STATE.resizeEdge = edge;
    TIMELINE_STATE.resizeStartY = e.clientY;

    startY = e.clientY;
    startTop = parseFloat(itemEl.style.top);
    startDuration = item.duration_mins;

    itemEl.style.opacity = "0.9";
    itemEl.style.zIndex = "1000";

    document.addEventListener("mousemove", onResizeMove);
    document.addEventListener("mouseup", onResizeEnd);
  };

  const onResizeMove = (e) => {
    const deltaY = e.clientY - startY;
    const deltaMins = Math.round(deltaY / TIMELINE_CONFIG.pixelsPerMinute / TIMELINE_CONFIG.G) * TIMELINE_CONFIG.G;

    let newDuration;
    if (TIMELINE_STATE.resizeEdge === "bottom") {
      newDuration = startDuration + deltaMins;
    } else {
      newDuration = startDuration - deltaMins;
    }

    if (newDuration < TIMELINE_CONFIG.minDuration) {
      itemEl.style.border = "2px solid #ef4444";
      TIMELINE_STATE.previewValid = false;
      return;
    }

    // Preview the resize
    const timeline = timedToTimelineItems(STATE.timed, rehearsalNum);
    const resizedItem = timeline.find(i => i.id === item.id);

    if (resizedItem) {
      const result = calculateResizeResult(resizedItem, newDuration, TIMELINE_STATE.resizeEdge, timeline, rehearsalNum);

      if (result.valid) {
        itemEl.style.border = "2px solid #22c55e";
        TIMELINE_STATE.previewValid = true;
        TIMELINE_STATE.previewTimeline = result.timeline;

        // Update visual height
        const newHeight = newDuration * TIMELINE_CONFIG.pixelsPerMinute;
        itemEl.style.height = newHeight + "px";

        // If resizing from top, adjust position
        if (TIMELINE_STATE.resizeEdge === "top") {
          const deltaHeight = (newDuration - startDuration) * TIMELINE_CONFIG.pixelsPerMinute;
          itemEl.style.top = (startTop - deltaHeight) + "px";
        }
      } else {
        itemEl.style.border = "2px solid #ef4444";
        TIMELINE_STATE.previewValid = false;
        console.log("Invalid resize:", result.errors);
      }
    }
  };

  const onResizeEnd = (e) => {
    document.removeEventListener("mousemove", onResizeMove);
    document.removeEventListener("mouseup", onResizeEnd);

    itemEl.style.opacity = "1";
    itemEl.style.zIndex = "10";
    itemEl.style.border = "";

    if (TIMELINE_STATE.previewValid && TIMELINE_STATE.previewTimeline) {
      // Apply the resize
      const beforeState = deepCopy(STATE.timed);

      // Update STATE.timed
      const updatedItems = timelineItemsToTimed(TIMELINE_STATE.previewTimeline);
      updatedItems.forEach(updated => {
        const idx = STATE.timed.findIndex(t => t._index === updated._index);
        if (idx !== -1) {
          STATE.timed[idx]['Time in Rehearsal'] = updated['Time in Rehearsal'];
          STATE.timed[idx]['Rehearsal Time (minutes)'] = updated['Rehearsal Time (minutes)'];
        }
      });

      const afterState = deepCopy(STATE.timed);

      // Push to history
      pushToHistory("resize", beforeState, afterState);

      // Save and re-render
      saveTimelineToBackend();
      renderTimelineEditor();
    } else {
      // Revert visual changes
      itemEl.style.height = (startDuration * TIMELINE_CONFIG.pixelsPerMinute) + "px";
      itemEl.style.top = startTop + "px";
      itemEl.style.animation = "shake 0.3s";
      setTimeout(() => itemEl.style.animation = "", 300);
    }

    TIMELINE_STATE.isDragging = false;
    TIMELINE_STATE.resizingItemId = null;
    TIMELINE_STATE.resizeEdge = null;
    TIMELINE_STATE.previewValid = false;
    TIMELINE_STATE.previewTimeline = null;
  };

  resizeTop.addEventListener("mousedown", onResizeStart("top"));
  resizeBottom.addEventListener("mousedown", onResizeStart("bottom"));
}

// ========================
// Timeline Editor: Save & History
// ========================

async function saveTimelineEdits(newTimed, action, description) {
  try {
    const dataToSave = newTimed || STATE.timed;
        const rehDur = getReharsalDurationMinutes(rnum);
        const totalBlocks = durationToBlocks(rehDur);
        const blockHeightPx = TIMELINE_CONFIG.blockHeight; // Use configured height from TIMELINE_CONFIG

        // Rehearsal column header
        const columnHeader = document.createElement("div");
        columnHeader.style.cssText = "padding:12px; background:#f3f4f6; border-bottom:1px solid #d1d5db; font-weight:600; text-align:center; border-radius:4px 4px 0 0;";
        columnHeader.textContent = `Rehearsal ${rnum}`;
        
        // Rehearsal column container
        const columnContainer = document.createElement("div");
        columnContainer.style.cssText = "flex: 0 0 350px; border:1px solid #d1d5db; border-radius:4px; overflow:hidden; background:white; display:flex; flex-direction:column;";
        
        // Add column header
        columnContainer.appendChild(columnHeader);

        // Timeline row (scrollable content)
        const rehearsalRow = document.createElement("div");
        rehearsalRow.style.cssText = `
          flex: 1 1 auto !important;
          display: block !important;
          overflow-y: auto !important;
          overflow-x: hidden !important;
          padding: 0 !important;
          background: white !important;
        `;

        // Time band container (vertical layout)
        const container = document.createElement("div");
        
        // Calculate container height based on maximum block extent
        const reh = rehearsals.find(r => parseInt(r.Rehearsal) === parseInt(rnum));
        const rehearsalStartTime = reh ? timeStringToMinutes(reh['Start Time']) : 0;
        
        let maxBlockBottom = totalBlocks * blockHeightPx; // Default: full rehearsal duration
        
        // Find the lowest block to determine container height
        works.forEach((w, idx) => {
          if (w.Title === "Break") return;
          const absTime = timeStringToMinutes(w['Time in Rehearsal']) || 0;
          const offset = Math.max(0, absTime - rehearsalStartTime);
          const dur = calculateWorkDuration(w, idx, works);
          const bottomPx = (offset + dur) / TIMELINE_CONFIG.G * blockHeightPx;
          maxBlockBottom = Math.max(maxBlockBottom, bottomPx);
        });
        
        container.style.cssText = `
          position: relative !important;
          width: 100% !important;
          height: ${Math.max(totalBlocks * blockHeightPx, maxBlockBottom + 20)}px !important;
          background: white !important;
          border: none !important;
          overflow: visible !important;
        `;
        
        for (let i = 0; i <= totalBlocks; i++) {
          const lineDiv = document.createElement("div");
          lineDiv.style.cssText = `
            position: absolute !important;
            left: 0 !important;
            right: 0 !important;
            top: ${i * blockHeightPx}px !important;
            height: 1px !important;
            background: ${i === 0 ? "#999" : "#e5e7eb"} !important;
            z-index: 0 !important;
          `;
          container.appendChild(lineDiv);
          
          // Add time labels on the left
          if (i % 2 === 0) {
            const timeLabel = document.createElement("div");
            const mins = i * TIMELINE_CONFIG.G;
            const absMin = rehearsalStartTime + mins;
            timeLabel.textContent = minutesToTimeString(absMin);
            timeLabel.style.cssText = `
              position: absolute !important;
              left: -50px !important;
              top: ${i * blockHeightPx - 10}px !important;
              width: 45px !important;
              text-align: right !important;
              font-size: 11px !important;
              color: #666 !important;
            `;
            container.appendChild(timeLabel);
          }
        }

        // Place work blocks
        works.forEach((work, workIdx) => {
          try {
            if (work.Title === "Break") return; // Skip breaks
            
            // Get absolute time and subtract rehearsal start to get offset within rehearsal
            // 1. SAFELY GET REHEARSAL START TIME
            const currentRehearsalData = STATE.rehearsals.find(r => parseInt(r.Rehearsal) === parseInt(rnum));
            
            let rehearsalStartMin = 0;
            if (currentRehearsalData && currentRehearsalData['Start Time']) {
              rehearsalStartMin = timeStringToMinutes(currentRehearsalData['Start Time']);
            } else if (works.length > 0 && works[0]['Time in Rehearsal']) {
              // Fallback: use first work's time as rehearsal start
              rehearsalStartMin = timeStringToMinutes(works[0]['Time in Rehearsal']);
            }

            // 2. CALCULATE POSITIONS
            const absStartMin = timeStringToMinutes(work['Time in Rehearsal']) || 0;
            let startMin = absStartMin - rehearsalStartMin;
            if (startMin < 0) startMin = 0; // Clamp negative values to 0
            
            // Get duration - use gap to next work if stored duration is missing
            const dur = calculateWorkDuration(work, workIdx, works);
            const blocks = durationToBlocks(dur);
            
            // 3. CALCULATE PIXELS
            const topOffset = (startMin / TIMELINE_CONFIG.G) * blockHeightPx;
            const height = blocks * blockHeightPx;

            console.log(`Block ${workIdx}: "${work.Title}", dur=${dur}min, blocks=${blocks}, height=${height}px, topOffset=${topOffset}px`);

            const blockEl = document.createElement("div");
            blockEl.className = "timeline-work-block";
            blockEl.id = `block-${rnum}-${workIdx}`;
            blockEl.dataset.rnum = rnum;
            blockEl.dataset.workIdx = workIdx;
            blockEl.dataset.origIndex = work._index;
            blockEl.dataset.rehearsalStartTime = rehearsalStartTime;
            blockEl.draggable = true;
            
            // Alternating block colors (blue and light blue)
            const blockColor = workIdx % 2 === 0 ? '#3b82f6' : '#93c5fd';
            const blockBorder = workIdx % 2 === 0 ? '#1e40af' : '#3b82f6';
            
            // Force inline styles (vertical layout)
            blockEl.style.cssText = `
              position: absolute !important;
              left: 0 !important;
              right: 0 !important;
              top: ${topOffset}px !important;
              height: ${height}px !important;
              background: ${blockColor} !important;
              border: 1px solid ${blockBorder} !important;
              color: white !important;
              padding: 8px 12px !important;
              border-radius: 3px !important;
              font-size: 12px !important;
              font-weight: 600 !important;
              cursor: grab !important;
              user-select: none !important;
              box-shadow: 0 2px 4px rgba(0,0,0,0.15) !important;
              display: flex !important;
              flex-direction: column !important;
              align-items: flex-start !important;
              gap: 4px !important;
              overflow: hidden !important;
              z-index: 1 !important;
              margin: 2px !important;
            `;

            const title = work.Title || "Untitled";
            // Use absolute times for display (not offsets)
            const endAbsMin = absStartMin + dur;
            const endTime = minutesToTimeString(endAbsMin);
            const startTime = work['Time in Rehearsal'] || "?";

            blockEl.innerHTML = `
              <div style="font-weight: 600; font-size: 12px;">${escapeHtml(title)}</div>
              <div style="font-size: 11px; opacity: 0.9;">${startTime}–${endTime}</div>
            `;
            
            // Add resize handle at bottom
            const resizeHandle = document.createElement("div");
            resizeHandle.className = "timeline-resize-handle";
            resizeHandle.style.cssText = "position: absolute !important; left: 0 !important; right: 0 !important; bottom: 0 !important; height: 4px !important; background: rgba(255,255,255,0.6) !important; cursor: ns-resize !important;";
            blockEl.appendChild(resizeHandle);

          // Drag handlers
          blockEl.addEventListener("dragstart", (e) => {
            TIMELINE_STATE.draggingBlockId = blockEl.id;
            TIMELINE_STATE.dragStartY = e.clientY;
            TIMELINE_STATE.dragStartOffset = topOffset;
            blockEl.classList.add("dragging");
            e.dataTransfer.effectAllowed = "move";
            e.dataTransfer.setData("text/plain", blockEl.id);
          });

          blockEl.addEventListener("dragend", (e) => {
            blockEl.classList.remove("dragging");
            document.querySelectorAll(".timeline-work-block").forEach(b => b.style.borderTop = "none");
            TIMELINE_STATE.draggedOverBlockId = null;
            TIMELINE_STATE.draggingBlockId = null;
          });

          blockEl.addEventListener("dragover", (e) => {
            e.preventDefault();
            e.dataTransfer.dropEffect = "move";
            
            // Highlight swap target
            if (TIMELINE_STATE.draggingBlockId && blockEl.id !== TIMELINE_STATE.draggingBlockId) {
              blockEl.style.borderTop = "3px solid #10b981";
              TIMELINE_STATE.draggedOverBlockId = blockEl.id;
            }
          });

          blockEl.addEventListener("dragleave", (e) => {
            if (e.target === blockEl) {
              blockEl.style.borderTop = "none";
              TIMELINE_STATE.draggedOverBlockId = null;
            }
          });

          blockEl.addEventListener("drop", (e) => {
            e.preventDefault();
            e.stopPropagation();
            blockEl.style.borderTop = "none";

            // Cascade drop logic
            if (TIMELINE_STATE.draggingBlockId && blockEl.id !== TIMELINE_STATE.draggingBlockId) {
              const draggedBlock = document.getElementById(TIMELINE_STATE.draggingBlockId);
              if (draggedBlock) {
                const dragRnum = parseInt(draggedBlock.dataset.rnum);
                const dropRnum = parseInt(blockEl.dataset.rnum);
                
                // Only cascade within same rehearsal
                if (dragRnum === dropRnum) {
                  const dragIndex = parseInt(draggedBlock.dataset.origIndex);
                  const dropIndex = parseInt(blockEl.dataset.origIndex);
                  
                  const draggedWork = STATE.timed[dragIndex];
                  const droppedWork = STATE.timed[dropIndex];
                  
                  // Get all works in this rehearsal to calculate current duration
                  const rehearsalWorks = STATE.timed.filter(w => w.Rehearsal === dragRnum && w.Title !== "Break");
                  const dragCurrentDuration = calculateWorkDuration(draggedWork, rehearsalWorks.indexOf(draggedWork), rehearsalWorks);
                  
                  // Move dragged block to where dropped block was
                  const newStartTime = droppedWork['Time in Rehearsal'];
                  draggedWork['Time in Rehearsal'] = newStartTime;
                  draggedWork['Rehearsal Time (minutes)'] = dragCurrentDuration;
                  
                  // Cascade other blocks
                  cascadeBlocksAfterMove(STATE.timed, dragIndex, newStartTime);
                  
                  renderTimelineEditor();
                  saveTimelineEdits(STATE.timed, "cascade", `Cascaded ${draggedBlock.dataset.title} to ${newStartTime}`);
                }
              }
            }
          });

          // Store title for swap description
          blockEl.dataset.title = title;

          // Mouse-based dragging with snap-to-grid (vertical layout)
          let isMouseDragging = false;
          let mouseStartY = 0;
          const originalTop = topOffset;

          blockEl.addEventListener("mousedown", (e) => {
            // Don't drag if clicking on resize handle
            if (e.target.closest(".timeline-resize-handle")) return;
            
            isMouseDragging = true;
            mouseStartY = e.clientY;
            blockEl.classList.add("dragging");
            blockEl.style.zIndex = "1000";
            document.body.style.cursor = "grabbing";
          });

          const onMouseMoveForDrag = (e) => {
            if (!isMouseDragging) return;
            
            const delta = e.clientY - mouseStartY;
            // Snap to grid: round to nearest blockHeightPx (5-minute block)
            const snappedDelta = Math.round(delta / blockHeightPx) * blockHeightPx;
            const newTop = Math.max(0, originalTop + snappedDelta);
            
            blockEl.style.top = newTop + "px";
          };

                    const onMouseUpForDrag = (e) => {
            if (!isMouseDragging) return;
            
            isMouseDragging = false;
            blockEl.classList.remove("dragging");
            blockEl.style.zIndex = "1"; // Reset z-index
            document.body.style.cursor = "grab";

            // Calculate new start time in minutes based on vertical position
            const newTopPx = parseFloat(blockEl.style.top);
            
            // Snap to grid logic
            const snapTo = 40; // blockHeightPx
            const snappedTop = Math.round(newTopPx / snapTo) * snapTo;
            
            // Calculate minutes offset from the snapped position
            const offsetMinutes = (snappedTop / snapTo) * TIMELINE_CONFIG.G;
            
            // Use the rehearsal start time from the dataset (avoids closure scope issues)
            const rehearsalStartTimeFromDataset = parseInt(blockEl.dataset.rehearsalStartTime);
            const newAbsMin = rehearsalStartTimeFromDataset + offsetMinutes;
            
            // Update STATE.timed
            const origIndex = parseInt(blockEl.dataset.origIndex);
            if (STATE.timed[origIndex]) {
              const newTimeStr = minutesToTimeString(newAbsMin);
              const oldTimeStr = STATE.timed[origIndex]['Time in Rehearsal'];
              
              // Don't recalculate duration here - cascade function will handle it
              // Just move the block to its new time
              STATE.timed[origIndex]['Time in Rehearsal'] = newTimeStr;
              
              console.log(`Moving block from ${oldTimeStr} to ${newTimeStr}`);
              
              // Cascade other blocks to fill gaps and recalculate all durations
              cascadeBlocksAfterMove(STATE.timed, origIndex, newTimeStr);
              
              // Force re-render to snap visually
              renderTimelineEditor();
              
              // Save to backend
              saveTimelineEdits(STATE.timed, "cascade", `Cascaded ${title} to ${newTimeStr}`);
            }
          };


          document.addEventListener("mousemove", onMouseMoveForDrag, true);
          document.addEventListener("mouseup", onMouseUpForDrag, true);

          // Cleanup listeners when block is removed from DOM
          blockEl._cleanupMouseDrag = () => {
            document.removeEventListener("mousemove", onMouseMoveForDrag, true);
            document.removeEventListener("mouseup", onMouseUpForDrag, true);
          };
          
          // Clean up old listeners before re-rendering
          blockEl.addEventListener("DOMNodeRemoved", blockEl._cleanupMouseDrag);
          
          // Attach event listener to the resize handle (now for vertical resize - height)
          resizeHandle.addEventListener("mousedown", (e) => {
            e.stopPropagation();
            TIMELINE_STATE.resizingBlockId = blockEl.id;
            TIMELINE_STATE.resizeStartY = e.clientY;
            TIMELINE_STATE.resizeStartHeight = height;
            TIMELINE_STATE.resizeStartBlocks = blocks;
            blockEl.classList.add("resizing");
            document.body.style.cursor = "ns-resize";
          });

          container.appendChild(blockEl);
          } catch (blockErr) {
            console.error(`Error creating block for work "${work.Title}":`, blockErr);
          }
        });
        
        console.log(`Rehearsal ${rnum}: Created ${works.filter(w => w.Title !== "Break").length} blocks in container, container height: ${totalBlocks * blockHeightPx}px`);
        const blockCount = container.querySelectorAll(".timeline-work-block").length;
        console.log(`DOM verification: Found ${blockCount} blocks actually in container`);

        rehearsalRow.appendChild(container);
        columnContainer.appendChild(rehearsalRow);
        gridDiv.appendChild(columnContainer);
      });

    contentEl.innerHTML = "";
    
    // Add no controls button - just the main grid
    contentEl.appendChild(gridDiv);
      // All rehearsals now always displayed, no controls needed
    
    console.log(`Timeline rendered: ${Object.keys(byRehearsal).length} rehearsals all visible side-by-side, ${STATE.timed.length} timed entries`);
    
    // Final verification
    const allBlocks = document.querySelectorAll(".timeline-work-block");
    console.log(`=== TIMELINE VERIFICATION ===`);
    console.log(`Total blocks in DOM: ${allBlocks.length}`);
    
    let blockInfo = `Total blocks created: ${allBlocks.length}\\n`;
    allBlocks.forEach((b, i) => {
      const rect = b.getBoundingClientRect();
      const posText = `top=${b.style.top}, height=${b.style.height}`;
      const displayText = rect.width > 0 && rect.height > 0 ? "✓ VISIBLE" : "✗ HIDDEN";
      const logMsg = `  Block ${i}: ${b.id}, ${posText}, ${displayText}`;
      console.log(logMsg);
      blockInfo += logMsg + "\\n";
    });
    
    // Create debug panel visible on page
    if (allBlocks.length > 0) {
      const debugPanel = document.createElement("div");
      debugPanel.style.cssText = `
        position: fixed !important;
        bottom: 10px !important;
        right: 10px !important;
        background: #222 !important;
        color: #0f0 !important;
        padding: 12px !important;
        border-radius: 4px !important;
        font-family: monospace !important;
        font-size: 11px !important;
        max-width: 400px !important;
        max-height: 200px !important;
        overflow: auto !important;
        z-index: 10000 !important;
        border: 1px solid #0f0 !important;
      `;
      debugPanel.innerHTML = blockInfo.replace(/\\n/g, "<br/>");
      document.body.appendChild(debugPanel);
    }

    // Global mouse handlers for drag and resize
    document.addEventListener("mousemove", onTimelineMouseMove, true);
    document.addEventListener("mouseup", onTimelineMouseUp, true);

  } catch (err) {
    console.error("Error rendering timeline:", err);
    contentEl.innerHTML = '<div style="padding:20px; color:red;">Error rendering timeline: ' + escapeHtml(err.message) + '</div>';
  }
}

function onTimelineMouseMove(e) {
  // Handle resizing (vertical layout - resizing changes height)
  if (TIMELINE_STATE.resizingBlockId) {
    const blockEl = document.getElementById(TIMELINE_STATE.resizingBlockId);
    if (blockEl) {
      const blockHeightPx = 40; // 40px per 5-min block
      const delta = e.clientY - TIMELINE_STATE.resizeStartY;
      const newBlocks = Math.max(1, TIMELINE_STATE.resizeStartBlocks + Math.round(delta / blockHeightPx));
      const newHeight = newBlocks * blockHeightPx;
      const newDuration = newBlocks * TIMELINE_CONFIG.G;
      
      blockEl.style.height = newHeight + "px";
      
      // Update the timed data in real-time
      const origIndex = parseInt(blockEl.dataset.origIndex);
      if (STATE.timed[origIndex]) {
        STATE.timed[origIndex]['Rehearsal Time (minutes)'] = newDuration;
      }
    }
  }
}

function onTimelineMouseUp(e) {
  if (TIMELINE_STATE.resizingBlockId) {
    const blockEl = document.getElementById(TIMELINE_STATE.resizingBlockId);
    if (blockEl) {
      blockEl.classList.remove("resizing");
    }
    document.body.style.cursor = "default";
    TIMELINE_STATE.resizingBlockId = null;
    saveTimelineEdits();
  }
}

function handleBlockDragOver(e) {
  e.preventDefault();
  e.dataTransfer.dropEffect = "move";
}

function handleBlockDrop(e) {
  e.preventDefault();
}

function handleBlockMouseDown(e) {
  // No-op: simplified timeline doesn't support drag yet
}

function handleBlockDragStart(e) {
  // No-op: simplified timeline doesn't support drag yet
}

function handleResizeStart(e) {
  // No-op: simplified timeline doesn't support resize yet
}

// ========================
// Timeline Editor: Save & History
// ========================

async function saveTimelineEdits(newTimed, action, description) {
  try {
    const dataToSave = newTimed || STATE.timed;
    
    // Debug: log what we're saving
    console.log(`Saving ${dataToSave.length} timed entries:`, dataToSave.map(t => ({
      Title: t.Title,
      'Time in Rehearsal': t['Time in Rehearsal'],
      'Rehearsal Time (minutes)': t['Rehearsal Time (minutes)']
    })));
    
    const res = await fetch(`${apiBase()}/timed_edit`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({
        timed: dataToSave,
        action: action || "edit",
        description: description || "Timeline modified"
      })
    });

    if (!res.ok) throw new Error(await res.text());
    const result = await res.json();

    STATE.timed = dataToSave;
    renderTimelineEditor();
    console.log("✓ Timeline saved. History entries:", result.history_len);
    
    // Show save notification
    const notif = document.createElement("div");
    notif.style.cssText = `
      position: fixed;
      top: 20px;
      right: 20px;
      background: #10b981;
      color: white;
      padding: 12px 20px;
      border-radius: 4px;
      font-size: 14px;
      z-index: 9999;
      box-shadow: 0 4px 6px rgba(0,0,0,0.1);
    `;
    notif.textContent = "✓ Changes saved";
    document.body.appendChild(notif);
    
    setTimeout(() => notif.remove(), 3000);
  } catch (err) {
    alert("Error saving timeline: " + err.message);
  }
}

async function loadTimelineHistory() {
  try {
    const history = await apiGet("/timed_history");
    if (!history || history.length === 0) {
      el("historyList").innerHTML = '<div style="color:#999;">No changes yet</div>';
      return;
    }

    let html = '';
    history.forEach(h => {
      const date = new Date(h.timestamp * 1000);
      const time = date.toLocaleTimeString();
      html += `<div class="history-item" onclick="revertToHistory(${h.index})">
        <div class="history-item-time">${time}</div>
        <div class="history-item-desc">${escapeHtml(h.description)}</div>
      </div>`;
    });
    el("historyList").innerHTML = html;
  } catch (err) {
    el("historyList").innerHTML = '<div style="color:red;">Failed to load history</div>';
  }
}

async function revertToHistory(historyIndex) {
  if (!confirm(`Revert to this version?`)) return;

  try {
    const res = await fetch(`${apiBase()}/timed_revert`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ history_index: historyIndex })
    });

    if (!res.ok) throw new Error(await res.text());

    await refreshFromServer();
    alert("✓ Reverted successfully");
  } catch (err) {
    alert("Error reverting: " + err.message);
  }
}

function toggleHistoryPanel() {
  const panel = el("historyPanel");
  panel.style.display = panel.style.display === "none" ? "block" : "none";
  if (panel.style.display === "block") {
    loadTimelineHistory();
  }
}

// --- GLOBAL EXPORTS ---
window.initEditor = initEditor;
window.uploadXlsx = uploadXlsx;
window.addWorkRow = addWorkRow;
window.addRehearsalRow = addRehearsalRow;
window.runAllocation = runAllocation;
window.generateSchedule = generateSchedule;
window.saveInputs = saveInputs;
window.refreshFromServer = refreshFromServer;
window.initEditor = initEditor;
window.renderTimelineEditor = renderTimelineEditor;
window.renderScheduleAccordion = renderScheduleAccordion;
window.renderTimedAccordion = renderTimedAccordion;
window.toggleHistoryPanel = toggleHistoryPanel;
window.toggleAccordion = toggleAccordion;
window.revertToHistory = revertToHistory;
window.handleBlockMouseDown = handleBlockMouseDown;
window.handleBlockDragStart = handleBlockDragStart;
window.handleBlockDragOver = handleBlockDragOver;
window.handleBlockDrop = handleBlockDrop;
window.handleResizeStart = handleResizeStart;
window.timelineUndo = timelineUndo;
window.timelineRedo = timelineRedo;