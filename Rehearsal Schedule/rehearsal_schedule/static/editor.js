let STATE = {
  works: [],
  works_cols: [],
  rehearsals: [],
  rehearsals_cols: [],
  allocation: [],
  allocation_original: [],  // Store original allocation for comparison (captured only once on first load)
  schedule: [],
  timed: [],
  _allocation_original_captured: false,  // Flag to ensure original allocation is only captured once
  conducting_insights: null  // Cache for conducting insights
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

// Helper to load and cache conducting insights
async function loadConductingInsights() {
  if (STATE.conducting_insights) {
    return STATE.conducting_insights;
  }
  
  const data = await apiGet('/conducting_insights');
  if (data.ok) {
    STATE.conducting_insights = data.insights;
    return data.insights;
  }
  return [];
}

// Format seconds as M:SS
function formatSecondsShort(seconds) {
  const mins = Math.floor(Math.abs(seconds) / 60);
  const secs = Math.abs(seconds) % 60;
  return `${mins}:${String(Math.round(secs)).padStart(2, '0')}`;
}

// --- ACCORDION TOGGLE ---

function toggleAccordion(headerEl) {
  const content = headerEl.nextElementSibling;
  const icon = headerEl.querySelector(".toggle-icon");
  
  const isCollapsed = headerEl.classList.contains("collapsed");
  
  if (isCollapsed) {
    // Open it
    headerEl.classList.remove("collapsed");
    content.style.display = "block";
  } else {
    // Close it
    headerEl.classList.add("collapsed");
    content.style.display = "none";
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
    const isTitle = isTitleColumn(c);
    const widthStyle = isTitle ? "min-width:400px; width:400px; max-width:400px;" : "min-width:120px; width:120px; max-width:120px;";
    th.style.cssText = `${widthStyle} box-sizing:border-box; padding:10px 8px; border:none; border-right:1px solid #e5e7eb; background:#f3f4f6; font-weight:600; text-align:left; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; height:44px; line-height:44px;`;
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

      const isTitle = isTitleColumn(c);
      const widthStyle = isTitle ? "min-width:400px; width:400px; max-width:400px;" : "min-width:120px; width:120px; max-width:120px;";
      td.appendChild(inp);
      td.style.cssText = `${widthStyle} box-sizing:border-box; padding:0; border:none; border-right:1px solid #e5e7eb; border-bottom:1px solid #e5e7eb; height:40px; line-height:40px; overflow:hidden;`;
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
    const widthStyle = isTitle ? "width:100%; min-width:400px;" : "width:90px; min-width:90px; max-width:90px;";
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
      const widthStyle = isTitle ? "width:100%; min-width:400px;" : "width:90px; min-width:90px; max-width:90px;";
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

// Helper: Derive current allocation from timed data
// Groups by Rehearsal and Title, sums the durations
// Track which items were directly edited by their _index AND (Title, Rehearsal)
let DIRECTLY_EDITED_INDICES = new Set();
let DIRECTLY_EDITED_PAIRS = new Set(); // Format: "Title|Rehearsal"

function buildTimeImpactTable() {
  /**
   * Impact view: Shows only works where allocation differs from required
   * Displays Required Minutes, Total Allocated, and breakdown by rehearsal
   * Required Minutes comes from original allocation (never changes)
   * Per-rehearsal allocation is summed from timeline editor (source of truth)
   */
  if (!STATE.timed || STATE.timed.length === 0) {
    return [];
  }
  
  // Build map of original required minutes
  const origMap = {};
  if (STATE.allocation_original && Array.isArray(STATE.allocation_original)) {
    STATE.allocation_original.forEach(row => {
      origMap[row.Title] = parseFloat(row['Required Minutes']) || 0;
    });
  }
  
  // Group timed data by Title and Rehearsal, tracking per-rehearsal totals
  const byTitleAndRehearsal = {}; // { "Title": { "Rehearsal 1": totalMins, "Rehearsal 2": totalMins, ... } }
  STATE.timed.forEach(row => {
    const title = row.Title;
    if (title === 'Break') return;
    
    const rehearsal = row.Rehearsal; // "Rehearsal 1", "Rehearsal 2", etc.
    let duration = parseFloat(row['Rehearsal Time (minutes)']) || parseFloat(row['Time in Rehearsal']) || 0;
    
    if (!byTitleAndRehearsal[title]) {
      byTitleAndRehearsal[title] = {};
    }
    
    if (!byTitleAndRehearsal[title][rehearsal]) {
      byTitleAndRehearsal[title][rehearsal] = 0;
    }
    byTitleAndRehearsal[title][rehearsal] += duration;
  });
  
  // Build impact table - only include works where Time Remaining != 0
  const result = [];
  Object.keys(byTitleAndRehearsal).sort().forEach(title => {
    const requiredMins = origMap[title] || 0;
    
    // Calculate total allocated across all rehearsals
    const rehearsalData = byTitleAndRehearsal[title];
    let totalAllocated = 0;
    for (let i = 1; i <= 6; i++) {
      const key = `Rehearsal ${i}`;
      if (rehearsalData[key]) {
        totalAllocated += rehearsalData[key];
      }
    }
    
    const timeRemaining = requiredMins - totalAllocated;
    
    // Only include if there's a difference (not perfectly allocated)
    if (timeRemaining !== 0) {
      const row = {
        'Title': title,
        'Required Minutes': requiredMins,
        'Total Allocated': totalAllocated
      };
      
      // Add per-rehearsal columns
      for (let i = 1; i <= 6; i++) {
        const key = `Rehearsal ${i}`;
        row[key] = rehearsalData[key] || 0;
      }
      
      result.push(row);
    }
  });
  
  return result;
}

// Render allocation with original vs current comparison - only for directly edited (Title, Rehearsal) pairs
function renderAllocationWithComparison(containerId, originalAlloc, currentAlloc) {
  const container = el(containerId);
  if (!container) return;
  container.innerHTML = "";
  
  if (!currentAlloc || currentAlloc.length === 0) {
    container.innerHTML = "<div class='msg'>No allocation data</div>";
    return;
  }
  
  // Get all rehearsal columns from the data (exclude 'Required Minutes', 'Time Remaining')
  const allCols = new Set();
  originalAlloc.forEach(row => {
    Object.keys(row).forEach(col => {
      if (col !== 'Title' && col !== 'Required Minutes' && col !== 'Time Remaining') {
        allCols.add(col);
      }
    });
  });
  
  const rehearsalCols = Array.from(allCols).sort((a, b) => {
    const aNum = parseInt(a.split(' ')[1]) || 0;
    const bNum = parseInt(b.split(' ')[1]) || 0;
    return aNum - bNum;
  });
  
  // Build maps
  const origMap = {};
  originalAlloc.forEach(row => {
    origMap[row.Title] = row;
  });
  
  const currMap = {};
  currentAlloc.forEach(row => {
    currMap[row.Title] = row;
  });
  
  // Collect unique titles from edited pairs and find which rehearsals they appear in
  const editedTitleMap = new Map(); // Title -> Set of rehearsal numbers
  DIRECTLY_EDITED_PAIRS.forEach(pair => {
    const [title, rnum] = pair.split('|');
    if (!editedTitleMap.has(title)) {
      editedTitleMap.set(title, new Set());
    }
    editedTitleMap.get(title).add(parseInt(rnum));
  });
  
  // For each edited title, find all rehearsals where it appears
  const titleRehearseMap = new Map(); // Title -> Set of all rehearsal numbers where it appears
  editedTitleMap.forEach((editedRnums, title) => {
    const origRow = origMap[title] || {};
    const appearsInRnums = new Set();
    
    rehearsalCols.forEach(col => {
      const val = parseFloat(origRow[col]) || 0;
      if (val > 0) {
        const rnum = parseInt(col.split(' ')[1]);
        appearsInRnums.add(rnum);
      }
    });
    
    titleRehearseMap.set(title, appearsInRnums);
  });
  
  // Build table header
  const table = document.createElement("table");
  table.className = "grid-table";
  table.style.cssText = "width:100%; border-collapse:collapse;";
  
  const thead = document.createElement("thead");
  const headerRow = document.createElement("tr");
  headerRow.style.cssText = "background:#f9fafb;";
  
  const titleTh = document.createElement("th");
  titleTh.textContent = "Title";
  titleTh.style.cssText = "border:1px solid #d1d5db; padding:8px; font-weight:600; text-align:left;";
  headerRow.appendChild(titleTh);
  
  const requiredTh = document.createElement("th");
  requiredTh.textContent = "Required Minutes";
  requiredTh.style.cssText = "border:1px solid #d1d5db; padding:8px; font-weight:600; text-align:left;";
  headerRow.appendChild(requiredTh);
  
  const timeRemainTh = document.createElement("th");
  timeRemainTh.textContent = "Time Remaining";
  timeRemainTh.style.cssText = "border:1px solid #d1d5db; padding:8px; font-weight:600; text-align:left;";
  headerRow.appendChild(timeRemainTh);
  
  // Add only the rehearsal columns that contain edited works
  const rehearsalsToShow = new Set();
  titleRehearseMap.forEach(rnums => {
    rnums.forEach(r => rehearsalsToShow.add(r));
  });
  
  const sortedRehearselToShow = Array.from(rehearsalsToShow).sort((a, b) => a - b);
  sortedRehearselToShow.forEach(rnum => {
    const th = document.createElement("th");
    th.textContent = `Rehearsal ${rnum}`;
    th.style.cssText = "border:1px solid #d1d5db; padding:8px; font-weight:600; text-align:left;";
    headerRow.appendChild(th);
  });
  
  thead.appendChild(headerRow);
  table.appendChild(thead);
  
  const tbody = document.createElement("tbody");
  
  // For each edited title (not each pair - just one row per title)
  editedTitleMap.forEach((editedRnums, title) => {
    const origRow = origMap[title] || {};
    const currRow = currMap[title] || {};
    
    const tr = document.createElement("tr");
    
    // Title cell
    const titleTd = document.createElement("td");
    const editRnumsStr = Array.from(editedRnums).sort((a, b) => a - b).join(", ");
    titleTd.textContent = `${title} (R${editRnumsStr})`;
    titleTd.style.cssText = "border:1px solid #d1d5db; padding:8px; font-weight:500;";
    tr.appendChild(titleTd);
    
    // Required Minutes cell
    const requiredTd = document.createElement("td");
    const requiredMins = parseFloat(origRow['Required Minutes']) || 0;
    requiredTd.textContent = Math.round(requiredMins);
    requiredTd.style.cssText = "border:1px solid #d1d5db; padding:8px; font-size:14px; font-weight:500;";
    tr.appendChild(requiredTd);
    
    // Time Remaining cell - Required minus total across all rehearsals
    const timeRemainTd = document.createElement("td");
    let totalAllocatedOrig = 0, totalAllocatedCurr = 0;
    
    rehearsalCols.forEach(col => {
      totalAllocatedOrig += parseFloat(origRow[col]) || 0;
      totalAllocatedCurr += parseFloat(currRow[col]) || 0;
    });
    
    const origTimeRemain = requiredMins - totalAllocatedOrig;
    const currTimeRemain = requiredMins - totalAllocatedCurr;
    
    const timeRemainText = `${Math.round(origTimeRemain)}→${Math.round(currTimeRemain)}`;
    let timeRemainBg = "#ffffff";
    
    if (Math.round(origTimeRemain) !== Math.round(currTimeRemain)) {
      if (currTimeRemain > origTimeRemain) {
        timeRemainBg = "#dcfce7";  // light green - more time remaining
      } else {
        timeRemainBg = "#fee2e2";  // light red - less time remaining
      }
    }
    
    timeRemainTd.textContent = timeRemainText;
    timeRemainTd.style.cssText = `border:1px solid #d1d5db; padding:8px; font-size:14px; background-color:${timeRemainBg}; font-weight:600;`;
    tr.appendChild(timeRemainTd);
    
    // Show only the rehearsal columns that are relevant
    sortedRehearselToShow.forEach(rnum => {
      const col = `Rehearsal ${rnum}`;
      const td = document.createElement("td");
      const origVal = parseFloat(origRow[col]) || 0;
      const currVal = parseFloat(currRow[col]) || 0;
      
      let displayText = `${Math.round(origVal)}→${Math.round(currVal)}`;
      let bgColor = "#ffffff";
      
      // Highlight if this rehearsal was edited for this title
      if (editedRnums.has(rnum)) {
        if (Math.round(origVal) !== Math.round(currVal)) {
          if (currVal > origVal) {
            bgColor = "#dcfce7";  // light green
          } else {
            bgColor = "#fee2e2";  // light red
          }
        }
      } else {
        // Not edited for this title - mute in gray
        bgColor = "#f5f5f5";
      }
      
      td.textContent = displayText;
      td.style.cssText = `border:1px solid #d1d5db; padding:8px; font-size:14px; background-color:${bgColor};`;
      tr.appendChild(td);
    });
    
    tbody.appendChild(tr);
  });
  
  if (DIRECTLY_EDITED_PAIRS.size === 0) {
    const emptyRow = document.createElement("tr");
    const emptyTd = document.createElement("td");
    emptyTd.textContent = "(No changes made)";
    emptyTd.style.cssText = "border:1px solid #d1d5db; padding:8px; color:#999;";
    emptyTd.colSpan = cols.length;
    emptyRow.appendChild(emptyTd);
    tbody.appendChild(emptyRow);
  }
  
  table.appendChild(tbody);
  container.appendChild(table);
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
    // Default header text
    let headerText = `Rehearsal ${rnum} (${items.length} items)`;
    // If this rehearsal is a Concert/Contest, show concert title and date instead
    const evTypeRaw = items[0]['Event Type'] || items[0]['Event_Type'] || items[0]['event'] || '';
    const evType = String(evTypeRaw).toLowerCase();
    if (evType === 'concert' || evType === 'contest') {
      const concertTitle = items[0]['Title'] || items[0]['Title'] === 0 ? items[0]['Title'] : (items[0]['Title'] || 'Concert');
      const dateVal = items[0]['Date'] || items[0]['date'] || '';
      let dateStr = '';
      try {
        if (dateVal) {
          const d = new Date(dateVal);
          if (!isNaN(d)) {
            dateStr = d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: undefined });
          }
        }
      } catch (e) { /* ignore */ }
      headerText = concertTitle + (dateStr ? ` — ${dateStr}` : '');
    }
    title.textContent = headerText;
    
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
    let isOpen = false; // Collapsed by default
    header.addEventListener("click", () => {
      isOpen = !isOpen;
      content.style.display = isOpen ? "block" : "none";
      toggle.style.transform = isOpen ? "rotate(180deg)" : "rotate(0deg)";
    });

    // Set initial state - collapsed
    content.style.display = "none";
    toggle.style.transform = "rotate(0deg)";

    // Add to accordion
    const section = document.createElement("div");
    section.appendChild(header);
    section.appendChild(content);
    section.style.cssText = "border-bottom:1px solid #d1d5db;";
    
    accordion.appendChild(section);
  });

  container.appendChild(accordion);
}

function renderAllocationAccordion(containerId, rows) {
  /**
   * Renders allocation table as an accordion, grouped by Title
   * Each row is a separate accordion item showing all columns
   */
  const container = el(containerId);
  if (!container) return;
  container.innerHTML = "";

  if (!rows || rows.length === 0) {
    container.innerHTML = "<div class='msg'>No allocation data</div>";
    return;
  }

  // Create accordion
  const accordion = document.createElement("div");
  accordion.style.cssText = "border:1px solid #d1d5db; border-radius:4px; overflow:hidden;";

  const cols = Object.keys(rows[0] || {});

  rows.forEach((row, idx) => {
    const title = row.Title || `Item ${idx + 1}`;
    const requiredMins = row['Required Minutes'] || 0;
    
    // Header (clickable)
    const header = document.createElement("div");
    header.style.cssText = "background:#f3f4f6; padding:12px 16px; border-bottom:1px solid #d1d5db; cursor:pointer; display:flex; justify-content:space-between; align-items:center;";
    
    const titleSpan = document.createElement("strong");
    titleSpan.textContent = `${title} (${requiredMins} min)`;
    
    const toggle = document.createElement("span");
    toggle.textContent = "▼";
    toggle.style.cssText = "font-size:12px; transition:transform 0.3s;";
    
    header.appendChild(titleSpan);
    header.appendChild(toggle);

    // Content (initially hidden)
    const content = document.createElement("div");
    content.style.cssText = "display:none; padding:12px;";
    
    // Render key-value pairs for this row
    const details = document.createElement("div");
    details.style.cssText = "display:grid; grid-template-columns:auto 1fr; gap:12px;";
    
    cols.forEach(col => {
      const label = document.createElement("div");
      label.textContent = col + ":";
      label.style.cssText = "font-weight:600; color:#374151;";
      
      const value = document.createElement("div");
      value.textContent = row[col] !== undefined ? row[col] : "-";
      value.style.cssText = "color:#6b7280;";
      
      details.appendChild(label);
      details.appendChild(value);
    });
    
    content.appendChild(details);

    // Toggle handler
    let isOpen = false;
    header.addEventListener("click", () => {
      isOpen = !isOpen;
      content.style.display = isOpen ? "block" : "none";
      toggle.style.transform = isOpen ? "rotate(180deg)" : "rotate(0deg)";
    });

    // Set initial state - collapsed
    content.style.display = "none";
    toggle.style.transform = "rotate(0deg)";

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

/**
 * Sort rehearsals chronologically and renumber them
 */
function sortAndRenumberRehearsals() {
  if (!STATE.rehearsals || STATE.rehearsals.length === 0) return;
  
  // Sort by date, then by start time
  STATE.rehearsals.sort((a, b) => {
    const dateA = a.Date || '';
    const dateB = b.Date || '';
    
    if (dateA !== dateB) {
      return dateA.localeCompare(dateB);
    }
    
    // If same date, sort by start time
    const timeA = a['Start Time'] || '';
    const timeB = b['Start Time'] || '';
    return timeA.localeCompare(timeB);
  });
  
  // Renumber sequentially
  STATE.rehearsals.forEach((reh, index) => {
    reh.Rehearsal = String(index + 1);
  });
  
  console.log("[SORT] Rehearsals sorted and renumbered chronologically:", 
    STATE.rehearsals.map(r => ({ num: r.Rehearsal, date: r.Date, time: r['Start Time'] })));
}

async function refreshFromServer() {
  const data = await apiGet("/state");
  STATE.works_cols = data.works_cols || [];
  STATE.rehearsals_cols = data.rehearsals_cols || [];
  STATE.works = data.works || [];
  STATE.rehearsals = data.rehearsals || [];
  STATE.ensemble_id = data.ensemble_id || STATE.ensemble_id;
  
  // Sort and renumber rehearsals chronologically
  sortAndRenumberRehearsals();
  
  STATE.allocation = data.allocation || [];
  // Run a preview allocation once per schedule session (client-side) so required minutes
  // are available when opening the editor. We track this with localStorage to avoid
  // re-running on every refresh/navigation.
  (async () => {
    try {
      const sid = scheduleId();
      const previewKey = `allocation_preview_done_${sid}`;
      const already = localStorage.getItem(previewKey);
      if (!already) {
        const preview = await apiPost('/run_allocation_preview', {});
        if (preview && preview.ok && preview.allocation && preview.allocation.length > 0) {
          console.log('[REFRESH] Applied preview allocation (client-side only). Rows:', preview.allocation.length);
          STATE.allocation = preview.allocation;
          localStorage.setItem(previewKey, String(Date.now()));
        }
      }
    } catch (e) {
      console.warn('[REFRESH] Allocation preview failed (silent):', e);
    }
  })();
  STATE.schedule = data.schedule || [];
  STATE.timed = (data.timed || []).map((row, idx) => ({ ...row, _index: idx }));
  STATE.concerts = data.concerts || [];  // Store concerts for timeline editor
  
  console.log("[REFRESH] Concerts loaded from API:", STATE.concerts);
  console.log("[REFRESH] Concerts count:", STATE.concerts.length);
  
  // Debug: Check if Event Type is being passed through
  if (STATE.rehearsals.length > 0) {
    const firstReh = STATE.rehearsals[0];
    const hasEventType = "Event Type" in firstReh;
    console.log("[TIMELINE] Rehearsals loaded:", {
      count: STATE.rehearsals.length,
      hasEventType: hasEventType,
      first_keys: Object.keys(firstReh),
      eventTypeValues: STATE.rehearsals.slice(0, 3).map(r => ({ Rehearsal: r.Rehearsal, "Event Type": r["Event Type"] }))
    });
  }
  
  console.log("STATE after refresh:", {
    works: STATE.works.length,
    rehearsals: STATE.rehearsals.length,
    allocation: STATE.allocation.length,
    allocation_original: STATE.allocation_original?.length,
    allocation_original_captured: STATE._allocation_original_captured,
    schedule: STATE.schedule.length,
    timed: STATE.timed.length
  });
  
  renderTable("works-table", STATE.works, STATE.works_cols, saveInputs);
  renderTable("rehearsals-table", STATE.rehearsals, STATE.rehearsals_cols, saveInputs);
  
  // Render output tables (read-only)
  console.log("Checking original allocation:", { has: !!STATE.allocation_original, length: STATE.allocation_original?.length, firstRow: STATE.allocation_original?.[0] });
  
  // Always render the ORIGINAL allocation table (never changes - captured once on init)
  if (STATE.allocation_original && STATE.allocation_original.length > 0) {
    const allocCols = Object.keys(STATE.allocation_original[0]);
    console.log("Rendering ORIGINAL allocation table with", STATE.allocation_original.length, "rows and columns:", allocCols);
    renderOutputTable("allocation-table", STATE.allocation_original, allocCols);
  } else {
    console.warn("⚠️ No original allocation data to render");
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
  openAddEventModal();
}

function addEventRow() {
  openAddEventModal();
}

async function runAllocation() {
  const msg = el("importMsg");
  msg.textContent = "Running allocation logic...";
  try {
    await saveInputs(true);
    const result = await apiPost("/run_allocation", {});
    
    // Reset the allocation capture flag so we recapture the newly generated allocation
    STATE._allocation_original_captured = false;
    const schedId = scheduleId();
    localStorage.removeItem(`allocation_original_${schedId}`);
    
    await refreshFromServer();
    msg.textContent = "Allocation complete.";
  } catch (err) {
    let errorMsg = "Allocation failed";
    try {
      // Try to parse JSON error response
      const errObj = JSON.parse(err.message);
      if (errObj.error) {
        errorMsg = `Allocation failed: ${errObj.error}`;
      }
    } catch {
      // If not JSON, use raw error message
      errorMsg = `Allocation failed: ${err.message}`;
    }
    msg.textContent = errorMsg;
    console.error("Allocation error:", err);
  }
}

/**
 * Sync dates from rehearsals table to timed data
 * When a rehearsal date is edited in the Rehearsals table, update all timed rows for that rehearsal
 */
function syncRehearsalDatesToTimed() {
  if (!STATE.timed || !STATE.rehearsals) return;
  
  // Build a map of rehearsal number -> date from the rehearsals table
  const rehearsalDateMap = {};
  
  // Find the Date column in rehearsals
  const dateColIndex = STATE.rehearsals_cols?.indexOf('Date') ?? -1;
  if (dateColIndex === -1) return;
  
  STATE.rehearsals.forEach((reh, idx) => {
    const rehNum = parseInt(reh['Rehearsal Number'] || reh['Rehearsal'] || idx + 1);
    const date = reh['Date'];
    if (date) {
      rehearsalDateMap[rehNum] = date;
    }
  });
  
  // Update timed data with these dates
  STATE.timed.forEach(timedRow => {
    const rehNum = parseInt(timedRow['Rehearsal']);
    if (rehearsalDateMap[rehNum]) {
      timedRow['Date'] = rehearsalDateMap[rehNum];
    }
  });
  
  console.log('✓ Synced rehearsal dates to timed data:', rehearsalDateMap);
}

async function generateSchedule() {
  const msg = el("importMsg");
  msg.textContent = "Generating schedule...";
  try {
    await apiPost("/generate_schedule", {});
    await refreshFromServer();
    // Sync rehearsal dates to timed data after generating
    syncRehearsalDatesToTimed();
    msg.textContent = "Schedule generated.";
  } catch (err) {
    let errorMsg = "Schedule generation failed";
    try {
      // Try to parse JSON error response
      const errObj = JSON.parse(err.message);
      if (errObj.error) {
        errorMsg = `Schedule generation failed: ${errObj.error}`;
      }
    } catch {
      // If not JSON, use raw error message
      errorMsg = `Schedule generation failed: ${err.message}`;
    }
    msg.textContent = errorMsg;
    console.error("Schedule generation error:", err);
  }
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
  // Refresh server state first
  await refreshFromServer();

  // After a successful import, automatically run allocation and generate schedule
  // so the timeline editor is populated for immediate editing.
  if (out.ok) {
    try {
      msg.textContent = 'Running allocation and generating schedule...';
      // runAllocation and generateSchedule are safe flows that persist to server
      await runAllocation();
      await generateSchedule();
      msg.textContent = 'Import, allocation and schedule generation complete.';
      // Final refresh to ensure client state is fully up-to-date
      await refreshFromServer();
    } catch (e) {
      console.error('Auto allocation/generation failed after import:', e);
      msg.textContent = 'Import succeeded; allocation/generation failed: ' + (e.message || e);
    }
  }
}

function initEditor(initial) {
  STATE = { ...STATE, ...initial };
  // Set schedule_id from the DOM
  STATE.schedule_id = scheduleId();
  
  // Ensure timed data has _index
  if (STATE.timed && Array.isArray(STATE.timed)) {
    STATE.timed = STATE.timed.map((row, idx) => ({ ...row, _index: idx }));
  }
  
  // Capture original allocation on VERY FIRST init (before any edits)
  // This runs only once when the page first loads
  if (STATE.allocation && STATE.allocation.length > 0 && !STATE._allocation_original_captured) {
    const schedId = scheduleId();
    const cacheKey = `allocation_original_${schedId}`;
    
    // Check if we have a cached version from a previous session
    const cached = localStorage.getItem(cacheKey);
    if (cached) {
      // Restore from cache
      try {
        STATE.allocation_original = JSON.parse(cached);
        console.log("✓ Restored original allocation from localStorage on init:", STATE.allocation_original.length, "rows");
      } catch (e) {
        // Cache is corrupted, use current allocation
        STATE.allocation_original = JSON.parse(JSON.stringify(STATE.allocation));
        localStorage.setItem(cacheKey, JSON.stringify(STATE.allocation_original));
        console.log("✓ Captured original allocation from initial data (cache was corrupted):", STATE.allocation_original.length, "rows");
      }
    } else {
      // First time - capture from the initial data passed in (this is the true original)
      STATE.allocation_original = JSON.parse(JSON.stringify(STATE.allocation));
      localStorage.setItem(cacheKey, JSON.stringify(STATE.allocation_original));
      console.log("✓ Captured and cached original allocation from initial data:", STATE.allocation_original.length, "rows");
    }
    STATE._allocation_original_captured = true;
  }
  
  renderTable("works-table", STATE.works, STATE.works_cols, saveInputs);
  renderTable("rehearsals-table", STATE.rehearsals, STATE.rehearsals_cols, saveInputs);
  
  // Store original allocation for comparison
  if (STATE.allocation && STATE.allocation.length > 0 && (!STATE.allocation_original || !STATE.allocation_original.length)) {
    STATE.allocation_original = JSON.parse(JSON.stringify(STATE.allocation));
  }
  
  // Initialize edited indices to all items on first load
  // If timed data is available, populate from timed (this is the source of truth for what was edited)
  // Otherwise, populate from allocation to show all available rows initially
  if (STATE.timed && Array.isArray(STATE.timed) && STATE.timed.length > 0) {
    // Timed data is available - use it as the source of truth
    DIRECTLY_EDITED_PAIRS.clear();
    DIRECTLY_EDITED_INDICES.clear();
    
    STATE.timed.forEach(row => {
      if (row.Title !== 'Break' && row._index !== undefined) {
        DIRECTLY_EDITED_INDICES.add(row._index);
        // Also add (Title, Rehearsal) pairs
        const rnum = parseInt(row.Rehearsal);
        if (!isNaN(rnum)) {
          DIRECTLY_EDITED_PAIRS.add(`${row.Title}|${rnum}`);
        }
      }
    });
  } else if (DIRECTLY_EDITED_INDICES.size === 0 && STATE.allocation && Array.isArray(STATE.allocation)) {
    // No timed data yet - populate pairs from allocation to show all rows initially
    DIRECTLY_EDITED_PAIRS.clear();
    STATE.allocation.forEach(row => {
      if (row.Title !== 'Break') {
        // Find all rehearsals where this work appears
        for (let rnum = 1; rnum <= 6; rnum++) {
          const col = `Rehearsal ${rnum}`;
          if (row[col] && row[col] > 0) {
            DIRECTLY_EDITED_PAIRS.add(`${row.Title}|${rnum}`);
          }
        }
      }
    });
  }
  
  // Render allocation table
  if (STATE.allocation && STATE.allocation.length > 0) {
    if (!STATE.allocation_original || !STATE.allocation_original.length) {
      STATE.allocation_original = JSON.parse(JSON.stringify(STATE.allocation));
    }
    const allocCols = Object.keys(STATE.allocation_original[0]);
    renderOutputTable("allocation-table", STATE.allocation_original, allocCols);
  }
  if (STATE.schedule && STATE.schedule.length > 0) {
    renderScheduleAccordion("schedule-table", STATE.schedule);
  }
  
  // Auto-generate allocation and schedule based on current state
  const hasWorks = STATE.works && STATE.works.length > 0;
  const hasRehearsals = STATE.rehearsals && STATE.rehearsals.length > 0;
  const hasAllocation = STATE.allocation && STATE.allocation.length > 0;
  const hasSchedule = STATE.schedule && STATE.schedule.length > 0;
  const hasTimed = STATE.timed && STATE.timed.length > 0;
  
  // Don't auto-generate - let user manually click the buttons
  // This prevents errors when data is incomplete or corrupted
  
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
  selectedIds: [],
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
  return String(h).padStart(2, "0") + ":" + String(m).padStart(2, "0") + ":00";
}

// Selection helpers
function isItemSelected(id) {
  return TIMELINE_STATE.selectedIds.includes(id);
}

function selectSingle(id) {
  TIMELINE_STATE.selectedIds = [id];
}

function toggleSelection(id) {
  if (isItemSelected(id)) {
    TIMELINE_STATE.selectedIds = TIMELINE_STATE.selectedIds.filter(x => x !== id);
  } else {
    TIMELINE_STATE.selectedIds = [...TIMELINE_STATE.selectedIds, id];
  }
}

function clearSelection() {
  TIMELINE_STATE.selectedIds = [];
}

function updateSelectionStyles() {
  document.querySelectorAll('.timeline-item').forEach(el => {
    const id = el.dataset.itemId || el.id;
    if (isItemSelected(id)) {
      el.style.outline = "3px solid #2563eb";
      el.style.boxShadow = "0 0 0 2px rgba(37,99,235,0.2)";
    } else {
      el.style.outline = "";
      el.style.boxShadow = "";
    }
  });
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
 * Convert timed array for all rehearsals or a single rehearsal into timeline items
 */
function timedToTimelineItems(timedArray, rehearsalNum = null) {
  // If no rehearsal specified, get all
  const rehearsalsToProcess = rehearsalNum !== null 
    ? [rehearsalNum] 
    : [...new Set(timedArray.map(w => parseInt(w.Rehearsal)).filter(r => !isNaN(r)))];
  
  let allItems = [];
  
  rehearsalsToProcess.forEach(rnum => {
    // Get works for this rehearsal and sort by time
    const rehearsalWorks = timedArray
      .filter(w => parseInt(w.Rehearsal) === rnum)
      .sort((a, b) => {
        const aMin = timeStringToMinutes(a['Time in Rehearsal']);
        const bMin = timeStringToMinutes(b['Time in Rehearsal']);
        return aMin - bMin;
      });
    
    // Get rehearsal bounds to calculate last item's duration
    const bounds = getRehearsalBounds(rnum);
    
    // Calculate durations from time gaps
    const items = rehearsalWorks.map((work, idx) => {
      let duration_mins = 5; // default
      
      // Check if duration is explicitly stored
      if (work['Rehearsal Time (minutes)']) {
        duration_mins = parseInt(work['Rehearsal Time (minutes)']);
      } 
      // For breaks, calculate from Break Start/End
      else if (work.Title === "Break" && work['Break Start (HH:MM)'] && work['Break End (HH:MM)']) {
        const breakStart = timeStringToMinutes(work['Break Start (HH:MM)']);
        const breakEnd = timeStringToMinutes(work['Break End (HH:MM)']);
        duration_mins = breakEnd - breakStart;
      }
      // Calculate from gap to next work
      else if (idx < rehearsalWorks.length - 1) {
        const currentStart = timeStringToMinutes(work['Time in Rehearsal']);
        const nextStart = timeStringToMinutes(rehearsalWorks[idx + 1]['Time in Rehearsal']);
        duration_mins = nextStart - currentStart;
      }
      // Last item: calculate from rehearsal end time
      else if (bounds) {
        const currentStart = timeStringToMinutes(work['Time in Rehearsal']);
        duration_mins = bounds.endMins - currentStart;
      }
      
      // Ensure minimum duration
      duration_mins = Math.max(5, duration_mins);
      
      return {
        id: work._index !== undefined ? `item-${work._index}` : `item-${idx}`,
        originalIndex: work._index !== undefined ? work._index : idx,
        title: work.Title || "Untitled",
        start: work['Time in Rehearsal'] || "14:00:00",
        start_time: work['Time in Rehearsal'] || "14:00:00",
        duration_mins: duration_mins,
        is_break: work.Title === "Break",
        rehearsalNum: rnum,
        rehearsal: rnum,
        date: work.Date,
        breakStart: work['Break Start (HH:MM)'],
        breakEnd: work['Break End (HH:MM)'],
        concert_id: work.concert_id,  // Preserve concert_id for concert events
      };
    });
    
    allItems = allItems.concat(items);
  });
  
  return allItems;
}

/**
 * Convert timeline items back to timed array entries
 */
function timelineItemsToTimed(items) {
  return items.map(item => {
    const startTime = item.start_time || item.start || "14:00:00";
    const entry = {
      _index: item.originalIndex,
      Title: item.title,
      'Time in Rehearsal': startTime,
      Rehearsal: item.rehearsalNum || item.rehearsal,
      Date: item.date || '',
      'Break Start (HH:MM)': item.breakStart || '',
      'Break End (HH:MM)': item.breakEnd || '',
    };
    
    // Preserve concert_id if present (for concert events)
    if (item.concert_id) {
      entry.concert_id = item.concert_id;
    }
    
    // Store duration if we have it
    if (item.duration_mins) {
      entry['Rehearsal Time (minutes)'] = item.duration_mins;
    }
    
    // Update break times if this is a break
    if (item.title === "Break" && item.duration_mins) {
      entry['Break Start (HH:MM)'] = startTime.substring(0, 5);
      const endTime = addMinutes(startTime, item.duration_mins);
      entry['Break End (HH:MM)'] = endTime.substring(0, 5);
    }
    
    return entry;
  });
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
  
  return items.map(item => {
    const start = currentTime;
    currentTime = addMinutes(currentTime, item.duration_mins);
    return {
      ...item,
      start: start,
      start_time: start
    };
  });
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
    console.log("Saving STATE.timed to backend...");
    
    // 1. Save the visual timeline edits
    const res = await fetch(`${apiBase()}/timed_edit`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({
        timed: STATE.timed,
        action: "timeline_edit",
        description: "Timeline modified"
      })
    });
    
    if (!res.ok) throw new Error(await res.text());
    const result = await res.json();
    console.log("Timeline auto-saved.", result);
    
    // Sync rehearsal dates to timed before re-rendering
    syncRehearsalDatesToTimed();
    
    // 5. Re-render the Original Allocation Table
    if (STATE.allocation_original && STATE.allocation_original.length > 0) {
      const allocCols = Object.keys(STATE.allocation_original[0]);
      console.log("Rendering original allocation table with columns:", allocCols);
      renderOutputTable("allocation-table", STATE.allocation_original, allocCols);
    }

    // 6. Re-render the Schedule Table (Accordion)
    if (STATE.schedule && STATE.schedule.length > 0) {
      renderScheduleAccordion("schedule-table", STATE.schedule);
    }
    
    // 7. Re-render Timed Text View
    if (STATE.timed && STATE.timed.length > 0) {
        renderTimedAccordion("timed-table", STATE.timed);
    }

    console.log("Tables updated successfully.");

  } catch (e) {
    console.error("Timeline save/refresh failed:", e);
  }
}


// ========================
// Timeline Rendering
// ========================

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

    // Group by rehearsal (STATE.timed already has _index)
    const byRehearsal = {};
    STATE.timed.forEach((row) => {
      const rnum = parseInt(row.Rehearsal);
      if (!byRehearsal[rnum]) byRehearsal[rnum] = [];
      byRehearsal[rnum].push(row);
    });

    // Preserve horizontal scroll position if present
    let prevScrollLeft = 0;
    const existingGrid = contentEl.querySelector('.timeline-grid');
    if (existingGrid) {
      try { prevScrollLeft = existingGrid.scrollLeft || 0; } catch (e) { prevScrollLeft = 0; }
    }

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

    // Restore previous horizontal scroll position so the view doesn't jump
    try {
      if (prevScrollLeft && gridDiv && typeof gridDiv.scrollLeft !== 'undefined') {
        gridDiv.scrollLeft = prevScrollLeft;
      }
    } catch (e) {
      console.warn('Could not restore timeline scroll position', e);
    }

    // Re-apply selection highlights after re-render
    updateSelectionStyles();

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
  column.setAttribute("data-rehearsal", rehearsalNum);
  column.style.cssText = "flex: 0 0 350px; border:1px solid #d1d5db; border-radius:4px; overflow:hidden; background:white; display:flex; flex-direction:column;";

  // Header with add button - determine event type and colors
  let eventType = "Rehearsal";
  let headerBgColor = "#f3f4f6";
  let headerBorderColor = "#d1d5db";
  let headerTextColor = "#000";
  
  if (STATE.rehearsals && Array.isArray(STATE.rehearsals)) {
    const rehearsalData = STATE.rehearsals.find(r => parseInt(r.Rehearsal) === rehearsalNum);
    if (rehearsalData) {
      eventType = rehearsalData['Event Type'] || 'Rehearsal';
      console.log(`[TIMELINE] Rehearsal ${rehearsalNum} Event Type: "${eventType}"`);
    }
  }
  
  // Normalize event type (trim whitespace)
  const eventTypeNormalized = eventType.trim();
  
  switch (eventTypeNormalized) {
    case 'Concert':
    case 'Contest':
      headerBgColor = "#dbeafe";
      headerBorderColor = "#0284c7";
      headerTextColor = "#0c4a6e";
      break;
    case 'Sectional':
      headerBgColor = "#fef3c7";
      headerBorderColor = "#f59e0b";
      headerTextColor = "#78350f";
      break;
    default:
      headerBgColor = "#f3f4f6";
      headerBorderColor = "#d1d5db";
      headerTextColor = "#000";
  }
  
  const header = document.createElement("div");
  header.style.cssText = `padding:12px; background:${headerBgColor}; border-bottom:2px solid ${headerBorderColor}; border-left:4px solid ${headerBorderColor}; font-weight:600; text-align:center; display:flex; justify-content:space-between; align-items:center; gap:8px;`;
  
  // Add double-click handler to header for opening edit modal
  header.ondblclick = () => openRehearsalEditModal(rehearsalNum);
  header.style.cursor = "pointer";
  header.title = "Double-click to edit event details";
  
  const title = document.createElement("span");
  
  // Determine title text based on event type
  let titleText = `Rehearsal ${rehearsalNum}`;
  if (eventTypeNormalized === 'Concert') {
    titleText = 'Concert';
  } else if (eventTypeNormalized === 'Contest') {
    titleText = 'Contest';
  } else if (eventTypeNormalized === 'Sectional') {
    // Get section name and work from rehearsal data
    let sectionName = '';
    let workName = '';
    if (STATE.rehearsals && Array.isArray(STATE.rehearsals)) {
      const rehearsalData = STATE.rehearsals.find(r => parseInt(r.Rehearsal) === rehearsalNum);
      if (rehearsalData) {
        sectionName = rehearsalData['Section'] || '';
        workName = rehearsalData['work'] || '';
      }
    }
    
    // Build title: "Sectional: Section - Work" or just "Sectional" if no details
    if (sectionName && workName) {
      titleText = `Sectional: ${sectionName} - ${workName}`;
    } else if (sectionName) {
      titleText = `Sectional: ${sectionName}`;
    } else if (workName) {
      titleText = `Sectional - ${workName}`;
    } else {
      titleText = 'Sectional';
    }
  }
  
  title.textContent = titleText;
  title.style.cssText = `flex: 1; color: ${headerTextColor};`;
  
  const viewBtn = document.createElement("button");
  viewBtn.textContent = "View";
  viewBtn.style.cssText = "padding:4px 12px; background:#10b981; color:white; border:none; border-radius:4px; font-size:12px; cursor:pointer; font-weight:500; white-space:nowrap;";
  viewBtn.addEventListener("click", () => {
    const sid = scheduleId();
    const token = getEditToken();
    const viewUrl = token ? `/s/${sid}?token=${token}&reh=${rehearsalNum}` : `/s/${sid}?reh=${rehearsalNum}`;
    window.open(viewUrl, '_blank');
  });
  
  const addBtn = document.createElement("button");
  addBtn.textContent = "+ Add";
  addBtn.style.cssText = "padding:4px 12px; background:#3b82f6; color:white; border:none; border-radius:4px; font-size:12px; cursor:pointer; font-weight:500; white-space:nowrap;";
  addBtn.addEventListener("click", () => openAddWorkModal(rehearsalNum));
  
  const editBtn = document.createElement("button");
  editBtn.textContent = "✎ Edit";
  editBtn.style.cssText = "padding:4px 12px; background:#f59e0b; color:white; border:none; border-radius:4px; font-size:12px; cursor:pointer; font-weight:500; white-space:nowrap;";
  editBtn.addEventListener("click", () => openRehearsalEditModal(rehearsalNum));
  
  const deleteBtn = document.createElement("button");
  deleteBtn.textContent = "🗑️";
  deleteBtn.style.cssText = "padding:4px 10px; background:#dc3545; color:white; border:none; border-radius:4px; font-size:14px; cursor:pointer; font-weight:bold; white-space:nowrap;";
  deleteBtn.title = "Delete this rehearsal";
  deleteBtn.addEventListener("click", () => deleteRehearsal(rehearsalNum));
  
  header.appendChild(title);
  header.appendChild(viewBtn);
  header.appendChild(addBtn);
  header.appendChild(editBtn);
  header.appendChild(deleteBtn);
  column.appendChild(header);

  // Timeline container (scrollable)
  const scrollContainer = document.createElement("div");
  scrollContainer.style.cssText = "flex: 1; overflow-y:auto; overflow-x:hidden; position:relative; display:flex;";

  // Calculate height based on rehearsal duration
  const durationMins = bounds.totalMins;
  const containerHeight = durationMins * TIMELINE_CONFIG.pixelsPerMinute + 40;

  // TIME AXIS - Fixed left column with time labels
  const timeAxis = document.createElement("div");
  timeAxis.style.cssText = `flex: 0 0 55px; height:${containerHeight}px; background:#f9fafb; border-right:2px solid #d1d5db; position:relative;`;
  
  const numLines = Math.ceil(durationMins / TIMELINE_CONFIG.G) + 1;
  for (let i = 0; i <= numLines; i++) {
    const mins = i * TIMELINE_CONFIG.G;
    const y = mins * TIMELINE_CONFIG.pixelsPerMinute;
    
    // Time label (every 10 minutes)
    if (i % 2 === 0) {
      const label = document.createElement("div");
      const absTime = addMinutes(bounds.startTime, mins);
      label.textContent = absTime.substring(0, 5); // HH:MM
      label.style.cssText = `position:absolute; left:0; right:0; top:${y - 8}px; font-size:11px; color:#4b5563; font-weight:500; text-align:center;`;
      timeAxis.appendChild(label);
    }
  }
  scrollContainer.appendChild(timeAxis);

  // Timeline content (with grid lines and works)
  const timelineContent = document.createElement("div");
  timelineContent.style.cssText = `flex: 1; position:relative; height:${containerHeight}px; background:white;`;
  timelineContent.dataset.rehearsalNum = rehearsalNum;

  // Add grid lines
  for (let i = 0; i <= numLines; i++) {
    const mins = i * TIMELINE_CONFIG.G;
    const y = mins * TIMELINE_CONFIG.pixelsPerMinute;
    
    // Grid line (thicker for 10-min marks)
    const line = document.createElement("div");
    const isMainLine = i % 2 === 0;
    line.style.cssText = `position:absolute; left:0; right:0; top:${y}px; height:1px; background:${isMainLine ? '#cbd5e1' : '#e5e7eb'}; z-index:0;`;
    timelineContent.appendChild(line);
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

  // Determine colors based on event type and whether it's a break
  let bgColor = '#3b82f6';  // Default rehearsal blue
  let borderColor = '#2563eb';
  
  if (item.is_break) {
    bgColor = '#f59e0b';  // Orange for breaks
    borderColor = '#d97706';
  } else {
    // Get event type for this rehearsal
    if (STATE.rehearsals && Array.isArray(STATE.rehearsals)) {
      const rehearsalData = STATE.rehearsals.find(r => parseInt(r.Rehearsal) === rehearsalNum);
      if (rehearsalData) {
        const eventType = rehearsalData['Event Type'] || 'Rehearsal';
        switch (eventType) {
          case 'Concert':
          case 'Contest':
            bgColor = '#06b6d4';  // Cyan/turquoise
            borderColor = '#0891b2';
            break;
          case 'Sectional':
            bgColor = '#ec4899';  // Pink/magenta
            borderColor = '#be185d';
            break;
          default: // Rehearsal
            bgColor = '#3b82f6';  // Blue
            borderColor = '#2563eb';
        }
      }
    }
  }

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

  if (isItemSelected(item.id)) {
    itemEl.style.outline = "3px solid #2563eb";
    itemEl.style.boxShadow = "0 0 0 2px rgba(37,99,235,0.2)";
  }

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
    position:relative;
  `;
  
  // Determine display title based on event type (for non-breaks)
  let displayTitle = item.title;
  if (!item.is_break && STATE.rehearsals && Array.isArray(STATE.rehearsals)) {
    const rehearsalData = STATE.rehearsals.find(r => parseInt(r.Rehearsal) === rehearsalNum);
    if (rehearsalData) {
      const eventType = rehearsalData['Event Type'] || 'Rehearsal';
      if (eventType === 'Concert') {
        displayTitle = 'Concert';
      } else if (eventType === 'Sectional') {
        displayTitle = 'Sectional Rehearsal';
      }
    }
  }
  
  // Determine what to show based on block size
  const isSmallBlock = item.duration_mins <= 10;
  const isTinyBlock = item.duration_mins <= 5;
  
  if (isTinyBlock) {
    // Tiny blocks: Just title, truncated
    content.innerHTML = `
      <div style="font-weight:600; font-size:11px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${escapeHtml(displayTitle)}</div>
    `;
  } else if (isSmallBlock) {
    // Small blocks: Title + start time only
    content.innerHTML = `
      <div style="font-weight:600; font-size:11px; margin-bottom:2px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${escapeHtml(displayTitle)}</div>
      <div style="font-size:10px; opacity:0.9;">${item.start_time.substring(0,5)}</div>
    `;
  } else {
    // Normal blocks: Full info
    content.innerHTML = `
      <div style="font-weight:600; font-size:12px; margin-bottom:4px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${escapeHtml(displayTitle)}</div>
      <div style="font-size:11px; opacity:0.9;">${item.start_time.substring(0,5)}–${endTime.substring(0,5)}</div>
      <div style="font-size:10px; opacity:0.8;">${item.duration_mins} min</div>
    `;
  }
  
  // Create hover tooltip with full details
  const tooltip = document.createElement("div");
  tooltip.className = "timeline-tooltip";
  tooltip.style.cssText = `
    position:absolute;
    left:100%;
    top:50%;
    transform:translateY(-50%);
    margin-left:12px;
    background:#1f2937;
    color:white;
    padding:10px 14px;
    border-radius:6px;
    font-size:12px;
    white-space:nowrap;
    z-index:2000;
    pointer-events:none;
    opacity:0;
    transition:opacity 0.2s;
    box-shadow:0 4px 12px rgba(0,0,0,0.3);
  `;
  tooltip.innerHTML = `
    <div style="font-weight:600; margin-bottom:6px;">${escapeHtml(displayTitle)}</div>
    <div style="font-size:11px; opacity:0.9; margin-bottom:2px;">Start: ${item.start_time.substring(0,5)}</div>
    <div style="font-size:11px; opacity:0.9; margin-bottom:2px;">End: ${endTime.substring(0,5)}</div>
    <div style="font-size:11px; opacity:0.9;">Duration: ${item.duration_mins} min</div>
  `;
  
  // Add tooltip arrow
  const arrow = document.createElement("div");
  arrow.style.cssText = `
    position:absolute;
    right:100%;
    top:50%;
    transform:translateY(-50%);
    width:0;
    height:0;
    border-top:6px solid transparent;
    border-bottom:6px solid transparent;
    border-right:6px solid #1f2937;
  `;
  tooltip.appendChild(arrow);
  
  content.appendChild(tooltip);
  
  // Show/hide tooltip on hover (only when not dragging)
  content.addEventListener("mouseenter", () => {
    itemEl.style.filter = "brightness(1.15)";
    if (!TIMELINE_STATE.isDragging) {
      tooltip.style.opacity = "1";
    }
  });
  content.addEventListener("mouseleave", () => {
    if (!TIMELINE_STATE.isDragging) {
      itemEl.style.filter = "brightness(1)";
      tooltip.style.opacity = "0";
    }
  });
  
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

  // Attach event handlers
  attachItemEventHandlers(itemEl, item, rehearsalNum, content, resizeTop, resizeBottom);

  return itemEl;
}

/**
 * Attach drag and resize event handlers to timeline item
 */
// ========================
// Drop Indicator Functions
// ========================

function updateDropIndicator(rehearsalNum, targetTime) {
  // Find or create drop indicator
  let indicator = document.getElementById(`drop-indicator-${rehearsalNum}`);
  if (!indicator) {
    // Find the rehearsal column by looking for the timeline content area
    const allColumns = document.querySelectorAll('[data-rehearsal]');
    let column = null;
    for (const col of allColumns) {
      if (col.getAttribute('data-rehearsal') == rehearsalNum) {
        column = col;
        break;
      }
    }
    
    if (!column) {
      console.warn(`Could not find rehearsal column ${rehearsalNum}`);
      return;
    }
    
    indicator = document.createElement('div');
    indicator.id = `drop-indicator-${rehearsalNum}`;
    indicator.style.cssText = `
      position: absolute;
      left: 55px;
      right: 0;
      height: 3px;
      background: #ef4444;
      z-index: 10000;
      pointer-events: none;
      box-shadow: 0 0 6px rgba(239, 68, 68, 0.8);
      display: none;
    `;
    
    // Find the timeline content area (the one with position: relative)
    const timelineContent = column.querySelector('[style*="position: relative"]');
    if (timelineContent) {
      timelineContent.appendChild(indicator);
      console.log(`Created drop indicator for rehearsal ${rehearsalNum}`);
    } else {
      console.warn('Could not find timeline content area');
      return;
    }
  }
  
  // Calculate position
  const bounds = getRehearsalBounds(rehearsalNum);
  if (!bounds) return;
  
  const startMins = timeStringToMinutes(bounds.startTime);
  const targetMins = timeStringToMinutes(targetTime);
  const offsetMins = targetMins - startMins;
  const top = offsetMins * TIMELINE_CONFIG.pixelsPerMinute;
  
  indicator.style.top = `${top}px`;
  indicator.style.display = 'block';
}

function hideDropIndicator(rehearsalNum) {
  const indicator = document.getElementById(`drop-indicator-${rehearsalNum}`);
  if (indicator) indicator.style.display = 'none';
}

// ========================
// Timeline Item Event Handlers
// ========================

function attachItemEventHandlers(itemEl, item, rehearsalNum, content, resizeTop, resizeBottom) {
  let startY = 0;
  let startTop = 0;
  let startDuration = 0;

  // Click to manage selection without dragging
  content.addEventListener("click", (e) => {
    // If we already dragged, ignore
    if (TIMELINE_STATE.isDragging) return;
    if (e.ctrlKey || e.metaKey) {
      toggleSelection(item.id);
    } else if (!isItemSelected(item.id)) {
      selectSingle(item.id);
    }
    updateSelectionStyles();
  });

  // MOVE: MouseDown on content
  const onMoveStart = (e) => {
    e.preventDefault();
    e.stopPropagation();

    // Selection handling: ctrl/cmd to toggle, otherwise keep existing multi-select if this item is already selected
    if (e.ctrlKey || e.metaKey) {
      toggleSelection(item.id);
    } else if (!isItemSelected(item.id)) {
      selectSingle(item.id);
    }

    // Restrict selection to this rehearsal for dragging
    const rehearsalItems = timedToTimelineItems(STATE.timed, rehearsalNum);
    const allowedIds = new Set(rehearsalItems.map(it => it.id));
    TIMELINE_STATE.selectedIds = TIMELINE_STATE.selectedIds.filter(id => allowedIds.has(id));
    updateSelectionStyles();

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
    
    const timeline = timedToTimelineItems(STATE.timed, rehearsalNum);
    const selectedIds = TIMELINE_STATE.selectedIds.length ? TIMELINE_STATE.selectedIds : [item.id];
    const selectedItems = timeline.filter(it => selectedIds.includes(it.id));

    // Multi-move: shift selected block together by delta
    if (selectedItems.length > 1) {
      const deltaMins = Math.round((snappedTop - startTop) / (TIMELINE_CONFIG.G * TIMELINE_CONFIG.pixelsPerMinute)) * TIMELINE_CONFIG.G;
      const shifted = selectedItems.map(si => ({
        ...si,
        start_time: addMinutes(si.start_time, deltaMins),
      }));
      const others = timeline.filter(it => !selectedIds.includes(it.id));
      const merged = [...others, ...shifted].sort((a, b) => timeStringToMinutes(a.start_time) - timeStringToMinutes(b.start_time));
      const compacted = compactTimeline(merged, rehearsalNum);
      const validation = validateTimeline(compacted, rehearsalNum);

      // Drop indicator at earliest shifted start
      const earliest = shifted.reduce((min, si) => Math.min(min, timeStringToMinutes(si.start_time)), Infinity);
      updateDropIndicator(rehearsalNum, addMinutes(bounds.startTime, earliest - timeStringToMinutes(bounds.startTime)));

      if (validation.valid) {
        itemEl.style.border = "2px solid #22c55e";
        TIMELINE_STATE.previewValid = true;
        TIMELINE_STATE.previewTimeline = compacted;
      } else {
        itemEl.style.border = "2px solid #ef4444";
        TIMELINE_STATE.previewValid = false;
      }
    } else {
      // Single-item move (existing behavior)
      const insertionPoints = [bounds.startTime];
      timeline.forEach(it => {
        if (it.id !== item.id) {
          const endTime = addMinutes(it.start_time, it.duration_mins);
          insertionPoints.push(endTime);
        }
      });
      const targetMins = timeStringToMinutes(targetTime);
      let closestPoint = insertionPoints[0];
      let closestDiff = Math.abs(timeStringToMinutes(closestPoint) - targetMins);
      insertionPoints.forEach(point => {
        const diff = Math.abs(timeStringToMinutes(point) - targetMins);
        if (diff < closestDiff) {
          closestDiff = diff;
          closestPoint = point;
        }
      });
      updateDropIndicator(rehearsalNum, closestPoint);

      const draggedItem = timeline.find(i => i.id === item.id);
      if (draggedItem) {
        const result = calculatePushResult(draggedItem, closestPoint, timeline, rehearsalNum);
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
    }
  };

  const onMoveEnd = (e) => {
    document.removeEventListener("mousemove", onMoveMove);
    document.removeEventListener("mouseup", onMoveEnd);
    
    hideDropIndicator(rehearsalNum);

    itemEl.style.opacity = "1";
    itemEl.style.cursor = "grab";
    content.style.cursor = "grab";
    itemEl.style.zIndex = "10";
    itemEl.style.border = "";

    console.log("Move ended. Preview valid:", TIMELINE_STATE.previewValid);

    if (TIMELINE_STATE.previewValid && TIMELINE_STATE.previewTimeline) {
      console.log("Applying move...");
      // Apply the move
      const beforeState = deepCopy(STATE.timed);
      
      // Track which item was directly edited
      DIRECTLY_EDITED_INDICES.add(item.originalIndex);
      DIRECTLY_EDITED_PAIRS.add(`${item.title}|${rehearsalNum}`);

      // Update STATE.timed with new timeline - preserve all original columns
      const updatedItems = timelineItemsToTimed(TIMELINE_STATE.previewTimeline);
      console.log("Updated items to save:", updatedItems);
      
      updatedItems.forEach(updated => {
        const idx = STATE.timed.findIndex(t => t._index === updated._index);
        console.log(`Looking for _index ${updated._index}, found at idx ${idx}`);
        if (idx !== -1) {
          console.log(`Updating index ${idx}: ${STATE.timed[idx].Title} from Rehearsal ${STATE.timed[idx].Rehearsal} to ${updated.Rehearsal}, time ${STATE.timed[idx]['Time in Rehearsal']} to ${updated['Time in Rehearsal']}`);
          // Preserve original data, only update time, rehearsal, and duration
          STATE.timed[idx]['Time in Rehearsal'] = updated['Time in Rehearsal'];
          STATE.timed[idx]['Rehearsal'] = updated['Rehearsal'];
          if (updated['Rehearsal Time (minutes)']) {
            STATE.timed[idx]['Rehearsal Time (minutes)'] = updated['Rehearsal Time (minutes)'];
          }
          
          // If this is a break, update break start/end times
          if (STATE.timed[idx].Title === "Break") {
            const startTime = updated['Time in Rehearsal'];
            const duration = updated['Rehearsal Time (minutes)'] || 0;
            const endTime = addMinutes(startTime, duration);
            STATE.timed[idx]['Break Start (HH:MM)'] = startTime.substring(0, 5); // HH:MM
            STATE.timed[idx]['Break End (HH:MM)'] = endTime.substring(0, 5); // HH:MM
            console.log(`Updated break times: ${STATE.timed[idx]['Break Start (HH:MM)']} - ${STATE.timed[idx]['Break End (HH:MM)']}`);
          }
        } else {
          console.error(`Could not find item with _index ${updated._index} in STATE.timed`);
        }
      });

      const afterState = deepCopy(STATE.timed);

      // Push to history
      pushToHistory("move", beforeState, afterState);

      // Save and re-render
      console.log("Calling saveTimelineToBackend...");
      saveTimelineToBackend();
      console.log("Calling renderTimelineEditor...");
      renderTimelineEditor();
    } else {
      console.warn("Move rejected - not valid. Snapping back.");
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

  // Double-click to open the same editor as the column header (concerts go to rehearsal edit)
  content.addEventListener("dblclick", (e) => {
    e.preventDefault();
    e.stopPropagation();
    
    // Check if this is a concert or sectional event
    let eventType = 'Rehearsal';
    
    if (STATE.rehearsals && Array.isArray(STATE.rehearsals)) {
      const rehearsalData = STATE.rehearsals.find(r => parseInt(r.Rehearsal) === rehearsalNum);
      if (rehearsalData) {
        eventType = rehearsalData['Event Type'] || 'Rehearsal';
      }
    }
    
    if (eventType === 'Concert') {
      // Match header edit button: open rehearsal edit modal for concerts
      openRehearsalEditModal(rehearsalNum);
    } else if (eventType === 'Sectional') {
      // Open event edit modal for sectionals
      openRehearsalEditModal(rehearsalNum);
    } else {
      // Show work details (total allocated vs required) instead of inline renaming
      openWorkDetailsModal(item, rehearsalNum);
    }
  });

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

    console.log("Resize ended. Preview valid:", TIMELINE_STATE.previewValid);

    if (TIMELINE_STATE.previewValid && TIMELINE_STATE.previewTimeline) {
      console.log("Applying resize...");
      // Apply the resize
      const beforeState = deepCopy(STATE.timed);
      
      // Track which item was directly edited
      DIRECTLY_EDITED_INDICES.add(item.originalIndex);
      DIRECTLY_EDITED_PAIRS.add(`${item.title}|${rehearsalNum}`);

      // Update STATE.timed - preserve all original columns
      const updatedItems = timelineItemsToTimed(TIMELINE_STATE.previewTimeline);
      console.log("Updated items to save:", updatedItems);
      
      updatedItems.forEach(updated => {
        const idx = STATE.timed.findIndex(t => t._index === updated._index);
        if (idx !== -1) {
          console.log(`Updating index ${idx}: ${STATE.timed[idx].Title} - time: ${updated['Time in Rehearsal']}, duration: ${updated['Rehearsal Time (minutes)']}`);
          // Preserve original data, only update time, rehearsal, and duration
          STATE.timed[idx]['Time in Rehearsal'] = updated['Time in Rehearsal'];
          STATE.timed[idx]['Rehearsal'] = updated['Rehearsal'];
          if (updated['Rehearsal Time (minutes)']) {
            STATE.timed[idx]['Rehearsal Time (minutes)'] = updated['Rehearsal Time (minutes)'];
          }
          
          // If this is a break, update break start/end times
          if (STATE.timed[idx].Title === "Break") {
            const startTime = updated['Time in Rehearsal'];
            const duration = updated['Rehearsal Time (minutes)'] || 0;
            const endTime = addMinutes(startTime, duration);
            STATE.timed[idx]['Break Start (HH:MM)'] = startTime.substring(0, 5); // HH:MM
            STATE.timed[idx]['Break End (HH:MM)'] = endTime.substring(0, 5); // HH:MM
            console.log(`Updated break times: ${STATE.timed[idx]['Break Start (HH:MM)']} - ${STATE.timed[idx]['Break End (HH:MM)']}`);
          }
        }
      });

      const afterState = deepCopy(STATE.timed);

      // Push to history
      pushToHistory("resize", beforeState, afterState);

      // Save and re-render
      console.log("Calling saveTimelineToBackend...");
      saveTimelineToBackend();
      console.log("Calling renderTimelineEditor...");
      renderTimelineEditor();
    } else {
      console.warn("Resize rejected - not valid. Reverting.");
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
// Work Details Modal
// ========================

async function openWorkDetailsModal(item, rehearsalNum) {
  // Find the original work in STATE.works to get orchestration info
  const workInfo = STATE.works.find(w => w.Title === item.title);
  
  // Compute required time using allocation if available; otherwise attempt to run allocation server-side
  let requiredTime = 0;
  const computeRequiredFromAllocation = (allocArray, title) => {
    let req = 0;
    if (!allocArray) return 0;
    const normTitle = String(title || '').trim().toLowerCase();
    const allocRow = allocArray.find(a => {
      const t = a.Title || a.Work || a.title || "";
      return String(t || '').trim().toLowerCase() === normTitle;
    });
    if (allocRow) {
      Object.keys(allocRow).forEach(key => {
        if (String(key).startsWith('Rehearsal ')) {
          const v = parseInt(allocRow[key]);
          if (!isNaN(v)) req += v;
        }
      });
    }
    return req;
  };

  // Try using cached allocation first
  requiredTime = computeRequiredFromAllocation(STATE.allocation, item.title);

  // If required time is zero, show a compute allocation action instead of auto-running
  let showComputeAlloc = false;
  if (requiredTime === 0) {
    showComputeAlloc = true;
  }

  // If we have no allocation on the client, attempt a preview allocation (doesn't overwrite server timed)
  if (showComputeAlloc && (!STATE.allocation || STATE.allocation.length === 0)) {
    try {
      const preview = await apiPost('/run_allocation_preview', {});
      if (preview && preview.ok && preview.allocation) {
        STATE.allocation = preview.allocation;
        // Recompute required time from the newly obtained allocation
        requiredTime = computeRequiredFromAllocation(STATE.allocation, item.title) || requiredTime;
        if (requiredTime > 0) showComputeAlloc = false;
      }
    } catch (e) {
      // Ignore preview errors here; user can click Compute allocation manually
      console.warn('Allocation preview failed (non-fatal):', e);
    }
  }

  // Fallback to works table if still not found
  if (requiredTime === 0 && workInfo) {
    requiredTime = parseInt(workInfo['Rehearsal Time Required']) || 0;
  }
  
  // Calculate TOTAL allocated time across ALL rehearsals from timeline items
  // (this reflects what's actually shown in the timeline editor)
  const allTimelineItems = timedToTimelineItems(STATE.timed);
  const totalAllocated = allTimelineItems
    .filter(timelineItem => timelineItem.title === item.title)
    .reduce((sum, timelineItem) => sum + (timelineItem.duration_mins || 0), 0);
  
  // Calculate difference
  const timeDiff = totalAllocated - requiredTime;
  const diffSign = timeDiff > 0 ? '+' : '';
  const diffColor = timeDiff > 0 ? '#10b981' : timeDiff < 0 ? '#ef4444' : '#6b7280';
  
  // Current rehearsal's allocation
  const currentAllocation = item.duration_mins;
  
  // Create modal overlay
  const overlay = document.createElement("div");
  overlay.style.cssText = `
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    bottom: 0;
    background: rgba(0,0,0,0.5);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 10000;
  `;
  
  // Create modal content
  const modal = document.createElement("div");
  modal.style.cssText = `
    background: white;
    border-radius: 8px;
    padding: 24px;
    max-width: 600px;
    width: 90%;
    max-height: 80vh;
    overflow-y: auto;
    box-shadow: 0 20px 25px -5px rgba(0,0,0,0.1), 0 10px 10px -5px rgba(0,0,0,0.04);
  `;
  
  // Build modal HTML with editable title and swap control
  let modalHTML = `
    <div style="display: flex; justify-content: space-between; align-items: start; margin-bottom: 20px;">
      <div style="flex:1;">
        <label style="display:block; font-size:12px; color:#6b7280; margin-bottom:6px;">Title (editable)</label>
        <input id="workTitleInput" value="${escapeHtml(item.title)}" style="width:100%; padding:8px; border:1px solid #e5e7eb; border-radius:6px; font-weight:600; font-size:16px;" />
      </div>
      <button onclick="this.closest('[style*=\\'position: fixed\\']').remove()" style="background: none; border: none; font-size: 24px; color: #6b7280; cursor: pointer; padding: 0; line-height: 1;">&times;</button>
    </div>
    
    <div style="margin-bottom: 20px;">
      <div style="font-size: 14px; color: #6b7280; margin-bottom: 8px;">
        <strong>Time in Rehearsal:</strong> ${item.start_time.substring(0,5)} – ${addMinutes(item.start_time, item.duration_mins).substring(0,5)}
      </div>
      <div style="font-size: 14px; color: #6b7280; margin-bottom: 8px;">
        <strong>Duration:</strong> ${item.duration_mins} minutes
      </div>
      <div style="font-size: 14px; color: #6b7280;">
        <strong>Rehearsal:</strong> ${rehearsalNum}
      </div>
    </div>
  `;
  
  // Add orchestration info if available
  if (workInfo) {
    const orchestration = workInfo['Orchestration'] || 'Not specified';
    const required = workInfo['Rehearsal Time Required'] || 'Not specified';
    
    modalHTML += `
      <div style="margin-bottom: 20px; padding: 16px; background: #f9fafb; border-radius: 6px;">
        <h4 style="margin: 0 0 12px 0; font-size: 14px; font-weight: 600; color: #374151;">Orchestration</h4>
        <div style="font-size: 13px; color: #4b5563; white-space: pre-wrap;">${escapeHtml(orchestration)}</div>
      </div>
    `;
  }
  
  modalHTML += `
    <div style="margin-bottom: 20px; padding: 16px; background: #f0f9ff; border-radius: 6px; border-left: 4px solid ${diffColor};">
      <h4 style="margin: 0 0 12px 0; font-size: 14px; font-weight: 600; color: #374151;">Time Allocation</h4>
      <div style="font-size: 13px; color: #4b5563; margin-bottom: 6px;">
        <strong>Required:</strong> <span id="requiredMinutes">${requiredTime}</span> minutes
        ${showComputeAlloc ? '<button id="computeAllocBtn" style="margin-left:12px; padding:6px 10px; font-size:12px; border-radius:6px; border:1px solid #d1d5db; background:#fff; cursor:pointer;">Compute allocation</button>' : ''}
      </div>
      <div style="font-size: 13px; color: #4b5563; margin-bottom: 6px;">
        <strong>Allocated (Total across all rehearsals):</strong> ${totalAllocated} minutes
      </div>
      <div style="font-size: 13px; color: #9ca3af; margin-bottom: 6px; font-style: italic;">
        Current rehearsal: ${currentAllocation} minutes
      </div>
      <div style="font-size: 14px; font-weight: 600; color: ${diffColor};">
        <strong>Difference:</strong> ${diffSign}${timeDiff} minutes
      </div>
    </div>
    
    <div id="conductingInsightsContainer" style="margin-bottom: 20px;">
      <div style="padding: 16px; background: #fef3c7; border-radius: 6px; text-align: center; color: #92400e;">
        <i>Loading conducting insights...</i>
      </div>
    </div>
  `;
  
  // Add action buttons
  modalHTML += `
    <div style="margin-top:12px; display:flex; gap:8px; align-items:center;">
      <div style="flex:1;">
        <label style="display:block; font-size:12px; color:#6b7280; margin-bottom:6px;">Or swap to another work:</label>
        <select id="swapWorkSelect" style="width:100%; padding:8px; border:1px solid #e5e7eb; border-radius:6px;">
          <option value="">-- keep current --</option>
          ${STATE.works.map((w, i) => `<option value="${i}">${escapeHtml(w.Title || w.title || '')}</option>`).join('')}
        </select>
      </div>
      <div style="display:flex; gap:8px;">
        ${showComputeAlloc ? '' : ''}
        <button id="saveWorkBtn" style="padding:10px 14px; background:#10b981; color:white; border:none; border-radius:6px; font-weight:600;">Save</button>
        <button id="deleteWorkBtn" style="padding:10px 14px; background:#ef4444; color:white; border:none; border-radius:6px; font-weight:600;">Delete</button>
        <button onclick="this.closest('[style*=\\'position: fixed\\']').remove()" style="padding:10px 14px; background:#6b7280; color:white; border:none; border-radius:6px; font-weight:600;">Close</button>
      </div>
    </div>
  `;
  
  modal.innerHTML = modalHTML;
  overlay.appendChild(modal);
  document.body.appendChild(overlay);
  
  // Load conducting insights asynchronously
  loadConductingInsights().then(insights => {
    const workInsight = insights.find(i => i.title === item.title);
    const container = document.getElementById('conductingInsightsContainer');
    
    if (workInsight && workInsight.total_times > 0) {
      const avgActualMins = Math.round(workInsight.avg_actual_seconds / 60);
      const avgActualSecs = workInsight.avg_actual_seconds % 60;
      const avgScheduledMins = Math.round(workInsight.avg_scheduled_seconds / 60);
      const avgVarianceSecs = workInsight.avg_variance_seconds;
      const avgVariancePct = workInsight.avg_variance_pct;
      const varianceColor = avgVarianceSecs > 0 ? '#ef4444' : avgVarianceSecs < 0 ? '#10b981' : '#6b7280';
      const varianceSign = avgVarianceSecs > 0 ? '+' : '';
      
      container.innerHTML = `
        <div style="padding: 16px; background: #f0fdf4; border-radius: 6px; border-left: 4px solid ${varianceColor};">
          <h4 style="margin: 0 0 12px 0; font-size: 14px; font-weight: 600; color: #374151;">
            Conducting History (${workInsight.total_times} time${workInsight.total_times > 1 ? 's' : ''})
          </h4>
          <div style="font-size: 13px; color: #4b5563; margin-bottom: 6px;">
            <strong>Avg Actual Time:</strong> ${avgActualMins}:${String(Math.round(avgActualSecs)).padStart(2, '0')} 
            (scheduled: ${avgScheduledMins} min)
          </div>
          <div style="font-size: 14px; font-weight: 600; color: ${varianceColor};">
            <strong>Avg Variance:</strong> ${varianceSign}${formatSecondsShort(Math.abs(avgVarianceSecs))} (${varianceSign}${avgVariancePct.toFixed(1)}%)
          </div>
          <div style="margin-top: 12px; padding-top: 12px; border-top: 1px solid #d1fae5;">
            <div style="font-size: 12px; color: #6b7280; font-style: italic;">
              Recent rehearsals:
            </div>
            ${workInsight.rehearsals.slice(-3).reverse().map(r => `
              <div style="font-size: 12px; color: #4b5563; margin-top: 4px;">
                R${r.rehearsal_num}: ${formatSecondsShort(r.actual_seconds)} 
                (${r.variance_seconds >= 0 ? '+' : ''}${formatSecondsShort(r.variance_seconds)})
              </div>
            `).join('')}
          </div>
        </div>
      `;
    } else {
      container.innerHTML = `
        <div style="padding: 16px; background: #f3f4f6; border-radius: 6px; text-align: center; color: #6b7280; font-size: 13px;">
          No conducting history yet for this work.
        </div>
      `;
    }
  }).catch(err => {
    console.error('Failed to load conducting insights:', err);
    const container = document.getElementById('conductingInsightsContainer');
    if (container) {
      container.innerHTML = `
        <div style="padding: 16px; background: #fef2f2; border-radius: 6px; text-align: center; color: #991b1b; font-size: 13px;">
          Failed to load conducting history.
        </div>
      `;
    }
  });
  
  // Add delete handler
  document.getElementById('deleteWorkBtn').addEventListener('click', () => {
    if (confirm(`Remove "${item.title}" from Rehearsal ${rehearsalNum}?`)) {
      deleteWorkFromTimeline(item.originalIndex);
      overlay.remove();
    }
  });

  // Save / swap handler
  document.getElementById('saveWorkBtn').addEventListener('click', async () => {
    const input = document.getElementById('workTitleInput');
    const select = document.getElementById('swapWorkSelect');
    const sel = select.value;
    const beforeState = deepCopy(STATE.timed);

    if (sel && sel !== '') {
      // Swap to selected work
      const workIdx = parseInt(sel);
      if (!isNaN(workIdx) && STATE.works[workIdx]) {
        const newTitle = STATE.works[workIdx].Title || STATE.works[workIdx].title || '';
        STATE.timed.forEach(t => {
          if (t._index === item.originalIndex) {
            t.Title = newTitle;
          }
        });
      }
    } else if (input) {
      const newTitle = input.value.trim();
      if (newTitle && newTitle !== item.title) {
        STATE.timed.forEach(t => {
          if (t._index === item.originalIndex) {
            t.Title = newTitle;
          }
        });
      }
    }

    const afterState = deepCopy(STATE.timed);
    pushToHistory('edit-title', beforeState, afterState);
    // Persist and re-render
    saveTimelineToBackend();
    renderTimelineEditor();
    overlay.remove();
  });

  // Compute allocation button handler (if present) — use preview to avoid overwriting timeline
  const computeBtn = document.getElementById('computeAllocBtn');
  if (computeBtn) {
    computeBtn.addEventListener('click', async () => {
      computeBtn.disabled = true;
      computeBtn.textContent = 'Computing...';
      try {
        const res = await apiPost('/run_allocation_preview', {});
        if (res && res.ok && res.allocation) {
          // Update only client-side allocation cache so we don't overwrite timed/schedule
          STATE.allocation = res.allocation;
          const newReq = (computeRequiredFromAllocation(STATE.allocation, item.title)) || 0;
          const reqEl = document.getElementById('requiredMinutes');
          if (reqEl) reqEl.textContent = String(newReq);
        } else {
          throw new Error((res && res.error) || 'No allocation returned');
        }
      } catch (err) {
        alert('Allocation preview failed: ' + (err.message || err));
      } finally {
        computeBtn.disabled = false;
        computeBtn.textContent = 'Compute allocation';
      }
    });
  }
  
  // Close on overlay click
  overlay.addEventListener('click', (e) => {
    if (e.target === overlay) overlay.remove();
  });
}

  // ========================
  // Find & Replace functions
  // ========================

  function _matchText(hay, needle, ignoreCase) {
    if (!hay || !needle) return false;
    if (ignoreCase) return String(hay).toLowerCase().includes(String(needle).toLowerCase());
    return String(hay).includes(String(needle));
  }

  function findReplacePreview() {
    const find = document.getElementById('fr-find').value || '';
    const replace = document.getElementById('fr-replace').value || '';
    const ignoreCase = !!document.getElementById('fr-ignore-case').checked;
    const scopeWorks = !!document.getElementById('fr-scope-works').checked;
    const scopeTimed = !!document.getElementById('fr-scope-timed').checked;
    const scopeConcerts = !!document.getElementById('fr-scope-concerts').checked;

    if (!find) {
      alert('Please enter text to find');
      return;
    }

    const results = { works: 0, timed: 0, concerts: 0, details: [] };

    if (scopeWorks && Array.isArray(STATE.works)) {
      STATE.works.forEach(w => {
        const title = w.Title || w.title || '';
        if (_matchText(title, find, ignoreCase)) {
          results.works += 1;
          results.details.push({type: 'work', id: w.Work || w.id || null, title});
        }
      });
    }

    if (scopeTimed && Array.isArray(STATE.timed)) {
      STATE.timed.forEach(t => {
        const title = t.Title || '';
        if (_matchText(title, find, ignoreCase)) {
          results.timed += 1;
          results.details.push({type: 'timed', index: t._index, title, rehearsal: t.Rehearsal});
        }
      });
    }

    if (scopeConcerts && Array.isArray(STATE.concerts)) {
      STATE.concerts.forEach(c => {
        const title = c.title || '';
        if (_matchText(title, find, ignoreCase)) {
          results.concerts += 1;
          results.details.push({type: 'concert', id: c.id, title});
        }
      });
    }

    const preview = document.getElementById('fr-preview');
    if (!preview) return;
    preview.innerHTML = `Preview: Works: <strong>${results.works}</strong>, Timeline: <strong>${results.timed}</strong>, Concerts: <strong>${results.concerts}</strong>`;
    if (results.details.length > 0) {
      preview.innerHTML += '<div style="margin-top:8px; max-height:160px; overflow:auto; border:1px solid #eee; padding:8px; border-radius:4px; background:#fafafa;">' +
        results.details.slice(0,200).map(d => {
          if (d.type === 'work') return `<div>Work: ${escapeHtml(d.title)}</div>`;
          if (d.type === 'timed') return `<div>Timed (R${d.rehearsal}): ${escapeHtml(d.title)}</div>`;
          return `<div>Concert: ${escapeHtml(d.title)}</div>`;
        }).join('') + '</div>';
    }
  }

  function applyFindReplaceAll(dryRun) {
    const find = document.getElementById('fr-find').value || '';
    const replace = document.getElementById('fr-replace').value || '';
    const ignoreCase = !!document.getElementById('fr-ignore-case').checked;
    const scopeWorks = !!document.getElementById('fr-scope-works').checked;
    const scopeTimed = !!document.getElementById('fr-scope-timed').checked;
    const scopeConcerts = !!document.getElementById('fr-scope-concerts').checked;

    if (!find) {
      alert('Please enter text to find');
      return;
    }

    const re = ignoreCase ? new RegExp(find.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'ig') : new RegExp(find.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'g');

    let workChanges = 0;
    let timedChanges = 0;
    let concertChanges = 0;

    if (scopeWorks && Array.isArray(STATE.works)) {
      STATE.works.forEach(w => {
        const key = w.Title ? 'Title' : (w.title ? 'title' : null);
        if (!key) return;
        if (String(w[key]).match(re)) {
          workChanges += 1;
          if (!dryRun) {
            w[key] = String(w[key]).replace(re, replace);
          }
        }
      });
    }

    if (scopeTimed && Array.isArray(STATE.timed)) {
      STATE.timed.forEach(t => {
        if (String(t.Title).match(re)) {
          timedChanges += 1;
          if (!dryRun) {
            t.Title = String(t.Title).replace(re, replace);
          }
        }
      });
    }

    if (scopeConcerts && Array.isArray(STATE.concerts)) {
      STATE.concerts.forEach(c => {
        if (String(c.title).match(re)) {
          concertChanges += 1;
          if (!dryRun) {
            c.title = String(c.title).replace(re, replace);
          }
        }
      });
    }

    const preview = document.getElementById('fr-preview');
    preview.innerHTML = `Replacements: Works: <strong>${workChanges}</strong>, Timeline: <strong>${timedChanges}</strong>, Concerts: <strong>${concertChanges}</strong>`;

    if (!dryRun) {
      // Persist changes: save works and timed
      if (workChanges > 0) {
        saveInputs();
        renderTable('works-table', STATE.works, STATE.works_cols, saveInputs);
      }
      if (timedChanges > 0) {
        // ensure indices exist
        STATE.timed = STATE.timed.map((t, idx) => ({ ...t, _index: t._index === undefined ? idx : t._index }));
        saveTimelineToBackend();
        renderTimelineEditor();
        renderTimedAccordion('timed-table', STATE.timed);
      }
      if (concertChanges > 0) {
        // Save concerts via backend if API exists
        try {
          apiPost('/update_concerts_bulk', { concerts: STATE.concerts }).catch(() => {});
        } catch (e) {}
      }
    }
  }

/**
 * Inline edit the title of a timeline item.
 * Updates STATE.timed entry and saves.
 */
function inlineEditItemTitle(itemEl, item, rehearsalNum) {
  // Prevent multiple editors
  if (itemEl.querySelector('.inline-title-input')) return;

  const originalTitle = item.title || '';

  // Create input overlay
  const input = document.createElement('input');
  input.type = 'text';
  input.value = originalTitle;
  input.className = 'inline-title-input';
  input.style.cssText = `position:absolute; left:8px; right:8px; top:8px; z-index:3000; padding:6px 8px; border-radius:4px; border:1px solid rgba(0,0,0,0.2); font-weight:600;`;

  // Append to the item content area
  itemEl.appendChild(input);
  input.focus();
  input.select();

  function commitChange() {
    const newTitle = input.value.trim();
    input.remove();
    if (newTitle === originalTitle) return;

    // Update STATE.timed entries matching this _index
    const beforeState = deepCopy(STATE.timed);
    let updatedCount = 0;
    STATE.timed.forEach(t => {
      if (t._index === item.originalIndex) {
        t.Title = newTitle;
        updatedCount++;
      }
    });

    // Mark edited indices/pairs
    DIRECTLY_EDITED_INDICES.add(item.originalIndex);
    DIRECTLY_EDITED_PAIRS.add(`${newTitle}|${rehearsalNum}`);

    if (updatedCount > 0) {
      const afterState = deepCopy(STATE.timed);
      pushToHistory('edit-title', beforeState, afterState);
      // Save timeline and re-render
      saveTimelineToBackend();
      renderTimelineEditor();
    }
  }

  function cancelEdit() {
    input.remove();
  }

  input.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter') {
      commitChange();
    } else if (ev.key === 'Escape') {
      cancelEdit();
    }
  });

  input.addEventListener('blur', () => {
    // Commit on blur
    commitChange();
  });
}

function deleteWorkFromTimeline(originalIndex) {
  const beforeState = deepCopy(STATE.timed);
  
  // Find the item being deleted to get its duration
  const deletedItem = STATE.timed.find(t => t._index === originalIndex);
  if (!deletedItem) return;
  
  const deletedDuration = parseInt(deletedItem['Rehearsal Time (minutes)']) || 5;
  const rehearsalNum = parseInt(deletedItem.Rehearsal);
  
  // Remove the work
  STATE.timed = STATE.timed.filter(t => t._index !== originalIndex);
  
  if (STATE.timed.length === 0) {
    // If all works deleted, just save empty state
    STATE.timed = [];
  } else {
    // Convert to timeline format
    let timelineItems = timedToTimelineItems(STATE.timed);
    
    // Redistribute the deleted duration to neighboring works
    const rehearsalItems = timelineItems.filter(it => it.rehearsalNum === rehearsalNum);
    const otherItems = timelineItems.filter(it => it.rehearsalNum !== rehearsalNum);
    
    if (rehearsalItems.length > 0) {
      // Find neighbors based on time position
      const bounds = getRehearsalBounds(rehearsalNum);
      if (bounds) {
        // Sort by start time
        rehearsalItems.sort((a, b) => timeStringToMinutes(a.start_time) - timeStringToMinutes(b.start_time));
        
        // Find where the deleted item was (by looking at the gap)
        let deletedIndex = -1;
        for (let i = 0; i < rehearsalItems.length - 1; i++) {
          const currentEnd = addMinutes(rehearsalItems[i].start_time, rehearsalItems[i].duration_mins);
          const nextStart = rehearsalItems[i + 1].start_time;
          if (currentEnd !== nextStart) {
            deletedIndex = i;
            break;
          }
        }
        
        // Check if deleted at the end
        if (deletedIndex === -1 && rehearsalItems.length > 0) {
          const lastItem = rehearsalItems[rehearsalItems.length - 1];
          const lastEnd = addMinutes(lastItem.start_time, lastItem.duration_mins);
          if (lastEnd !== bounds.endTime) {
            deletedIndex = rehearsalItems.length - 1;
          }
        }
        
        // Redistribute the duration
        if (deletedDuration <= 5) {
          // Small gap (≤5 mins): give to next item (or previous if at end)
          if (deletedIndex >= 0 && deletedIndex < rehearsalItems.length - 1) {
            rehearsalItems[deletedIndex + 1].duration_mins += deletedDuration;
          } else if (rehearsalItems.length > 0) {
            rehearsalItems[rehearsalItems.length - 1].duration_mins += deletedDuration;
          }
        } else {
          // Larger gap (>5 mins): split between neighbors
          const prevItem = deletedIndex >= 0 ? rehearsalItems[deletedIndex] : null;
          const nextItem = deletedIndex >= 0 && deletedIndex < rehearsalItems.length - 1 ? rehearsalItems[deletedIndex + 1] : null;
          
          if (prevItem && nextItem) {
            // Split between both neighbors
            const half = Math.floor(deletedDuration / 2);
            prevItem.duration_mins += half;
            nextItem.duration_mins += (deletedDuration - half);
          } else if (prevItem) {
            // Only previous exists, give to it
            prevItem.duration_mins += deletedDuration;
          } else if (nextItem) {
            // Only next exists, give to it
            nextItem.duration_mins += deletedDuration;
          }
        }
        
        // Recalculate start times
        let currentTime = bounds.startTime;
        rehearsalItems.forEach(item => {
          item.start = currentTime;
          item.start_time = currentTime;
          currentTime = addMinutes(currentTime, item.duration_mins);
        });
      }
      
      timelineItems = [...otherItems, ...rehearsalItems];
    }
    
    // Convert back to timed format
    STATE.timed = timelineItemsToTimed(timelineItems);
  }
  
  // Re-index remaining items
  STATE.timed = STATE.timed.map((t, idx) => ({ ...t, _index: idx }));
  
  const afterState = deepCopy(STATE.timed);
  
  // Push to history
  pushToHistory("delete", beforeState, afterState);
  
  // Save and re-render
  saveTimelineToBackend();
  renderTimelineEditor();
  
  // Update allocation and schedule views
  refreshComputedTables();
}

function openAddWorkModal(rehearsalNum) {
  // Get works that are available to add
  const availableWorks = STATE.works.filter(w => w.Title && w.Title.trim() !== '');
  
  if (availableWorks.length === 0) {
    alert("No works available to add. Please add works in the Works table first.");
    return;
  }
  
  // Create modal overlay
  const overlay = document.createElement("div");
  overlay.style.cssText = `
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    bottom: 0;
    background: rgba(0,0,0,0.5);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 10000;
  `;
  
  // Create modal content
  const modal = document.createElement("div");
  modal.style.cssText = `
    background: white;
    border-radius: 8px;
    padding: 24px;
    max-width: 600px;
    width: 90%;
    max-height: 80vh;
    overflow-y: auto;
    box-shadow: 0 20px 25px -5px rgba(0,0,0,0.1), 0 10px 10px -5px rgba(0,0,0,0.04);
  `;
  
  let modalHTML = `
    <div style="display: flex; justify-content: space-between; align-items: start; margin-bottom: 20px;">
      <h3 style="margin: 0; font-size: 18px; font-weight: 600; color: #111827;">Add Work to Rehearsal ${rehearsalNum}</h3>
      <button onclick="this.closest('[style*=\\'position: fixed\\']').remove()" style="background: none; border: none; font-size: 24px; color: #6b7280; cursor: pointer; padding: 0; line-height: 1;">&times;</button>
    </div>
    
    <div style="margin-bottom: 16px;">
      <label style="display: block; font-size: 14px; font-weight: 500; color: #374151; margin-bottom: 8px;">Select Work:</label>
      <select id="workSelect" style="width: 100%; padding: 10px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 14px;">
        <option value="">-- Choose a work --</option>
  `;
  
  availableWorks.forEach((work, idx) => {
    const required = work['Rehearsal Time Required'] || '?';
    modalHTML += `<option value="${idx}">${escapeHtml(work.Title)} (${required} min)</option>`;
  });
  
  modalHTML += `
      </select>
    </div>
    
    <div style="margin-bottom: 16px;">
      <label style="display: block; font-size: 14px; font-weight: 500; color: #374151; margin-bottom: 8px;">Duration (minutes):</label>
      <input type="number" id="durationInput" value="5" min="5" step="5" style="width: 100%; padding: 10px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 14px;">
    </div>
    
    <div style="margin-bottom: 20px;">
      <label style="display: block; font-size: 14px; font-weight: 500; color: #374151; margin-bottom: 8px;">Insert Position:</label>
      <select id="positionSelect" style="width: 100%; padding: 10px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 14px;">
        <option value="end">End of rehearsal</option>
        <option value="start">Start of rehearsal</option>
      </select>
    </div>
    
    <div style="display: flex; gap: 12px;">
      <button id="addWorkBtn" style="flex: 1; padding: 10px; background: #3b82f6; color: white; border: none; border-radius: 6px; font-size: 14px; font-weight: 500; cursor: pointer;">
        Add Work
      </button>
      <button onclick="this.closest('[style*=\\'position: fixed\\']').remove()" style="flex: 1; padding: 10px; background: #6b7280; color: white; border: none; border-radius: 6px; font-size: 14px; font-weight: 500; cursor: pointer;">
        Cancel
      </button>
    </div>
  `;
  
  modal.innerHTML = modalHTML;
  overlay.appendChild(modal);
  document.body.appendChild(overlay);
  
  // Add handler
  document.getElementById('addWorkBtn').addEventListener('click', () => {
    const selectEl = document.getElementById('workSelect');
    const workIdx = parseInt(selectEl.value);
    const duration = parseInt(document.getElementById('durationInput').value) || 5;
    const position = document.getElementById('positionSelect').value;
    
    if (isNaN(workIdx) || workIdx < 0) {
      alert("Please select a work");
      return;
    }
    
    const work = availableWorks[workIdx];
    addWorkToTimeline(work, rehearsalNum, duration, position);
    overlay.remove();
  });
  
  // Auto-fill duration when work selected
  document.getElementById('workSelect').addEventListener('change', (e) => {
    const idx = parseInt(e.target.value);
    if (!isNaN(idx)) {
      const work = availableWorks[idx];
      const required = parseInt(work['Rehearsal Time Required']) || 5;
      document.getElementById('durationInput').value = required;
    }
  });
  
  // Add Enter key handler
  modal.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      document.getElementById('addWorkBtn').click();
    }
  });
  
  // Close on overlay click
  overlay.addEventListener('click', (e) => {
    if (e.target === overlay) overlay.remove();
  });
}

/**
 * Open modal to edit rehearsal details (date, time, section)
 */
function openRehearsalEditModal(rehearsalNum) {
  // Find rehearsal in STATE.rehearsals
  let rehearsalData = null;
  if (STATE.rehearsals && Array.isArray(STATE.rehearsals)) {
    rehearsalData = STATE.rehearsals.find(r => parseInt(r.Rehearsal) === rehearsalNum);
  }
  
  const date = rehearsalData && rehearsalData['Date'] ? rehearsalData['Date'].substring(0, 10) : '';
  const startTime = rehearsalData && rehearsalData['Start Time'] ? rehearsalData['Start Time'] : '';
  const endTime = rehearsalData && rehearsalData['End Time'] ? rehearsalData['End Time'] : '';
  const includeInAllocation = rehearsalData && rehearsalData['Include in allocation'] ? rehearsalData['Include in allocation'] : 'Y';
  const eventType = rehearsalData && rehearsalData['Event Type'] ? rehearsalData['Event Type'] : 'Rehearsal';
  const section = rehearsalData && rehearsalData['Section'] ? rehearsalData['Section'] : 'Full Ensemble';
  const isConcert = eventType === 'Concert' || eventType === 'Contest';
  
  // For concerts, get concert data from STATE.concerts using concert_id from timed data
  let concertData = null;
  if (isConcert && STATE.concerts && STATE.timed) {
    console.log('[CONCERT EDIT MODAL] Looking for concert data for rehearsal', rehearsalNum);
    console.log('[CONCERT EDIT MODAL] STATE.concerts:', STATE.concerts);
    console.log('[CONCERT EDIT MODAL] STATE.timed length:', STATE.timed.length);
    
    const timedRow = STATE.timed.find(t => parseInt(t.Rehearsal) === rehearsalNum);
    console.log('[CONCERT EDIT MODAL] Found timed row:', timedRow);
    
    if (timedRow && timedRow.concert_id) {
      console.log('[CONCERT EDIT MODAL] Looking for concert with id:', timedRow.concert_id);
      concertData = STATE.concerts.find(c => c.id === timedRow.concert_id);
      console.log('[CONCERT EDIT MODAL] Found concert data:', concertData);
    } else {
      console.log('[CONCERT EDIT MODAL] No concert_id in timed row');
    }
  } else {
    console.log('[CONCERT EDIT MODAL] Skipping concert lookup:', {
      isConcert,
      hasConcerts: !!STATE.concerts,
      hasTimed: !!STATE.timed
    });
  }
  
  // Extract concert details for pre-populating fields
  const concertTitle = concertData?.title || '';
  const concertVenue = concertData?.venue || '';
  const concertUniform = concertData?.uniform || '';
  const concertProgramme = concertData?.programme || '';
  const concertOtherInfo = concertData?.other_info || '';
  const concertTime = concertData?.time || startTime;
  // Use concert date if available, otherwise use rehearsal date
  const concertDate = concertData?.date ? concertData.date.substring(0, 10) : date;
  
  // Create modal overlay
  const overlay = document.createElement("div");
  overlay.id = "rehearsalEditModalOverlay";
  overlay.style.cssText = `
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    bottom: 0;
    background: rgba(0,0,0,0.5);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 10000;
  `;
  
  // Create modal content
  const modal = document.createElement("div");
  modal.style.cssText = `
    background: white;
    border-radius: 8px;
    padding: 24px;
    max-width: 500px;
    width: 90%;
    max-height: 80vh;
    overflow-y: auto;
    box-shadow: 0 20px 25px -5px rgba(0,0,0,0.1), 0 10px 10px -5px rgba(0,0,0,0.04);
  `;
  
  // Build modal content - different for concerts vs rehearsals
  if (isConcert) {
    modal.innerHTML = `
      <div style="display: flex; justify-content: space-between; align-items: start; margin-bottom: 20px;">
        <h3 style="margin: 0; font-size: 18px; font-weight: 600; color: #111827;">Edit Concert</h3>
        <button onclick="document.getElementById('rehearsalEditModalOverlay')?.remove()" style="background: none; border: none; font-size: 24px; color: #6b7280; cursor: pointer; padding: 0; line-height: 1;">&times;</button>
      </div>
      
      <div style="margin-bottom: 16px;">
        <label style="display: block; font-size: 14px; font-weight: 500; color: #374151; margin-bottom: 8px;">Concert Title</label>
        <input type="text" id="editConcertTitle" value="${concertTitle}" placeholder="e.g., Spring Concert 2026" style="width: 100%; padding: 10px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 14px; box-sizing: border-box;">
        <small style="color: #6b7280; display: block; margin-top: 4px;">Title of the concert</small>
      </div>
      
      <div style="margin-bottom: 16px;">
        <label style="display: block; font-size: 14px; font-weight: 500; color: #374151; margin-bottom: 8px;">Date</label>
        <input type="date" id="editConcertDate" value="${concertDate}" style="width: 100%; padding: 10px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 14px; box-sizing: border-box;">
        <small style="color: #6b7280; display: block; margin-top: 4px;">Date of the concert</small>
      </div>
      
      <div style="margin-bottom: 16px;">
        <label style="display: block; font-size: 14px; font-weight: 500; color: #374151; margin-bottom: 8px;">Time (HH:MM)</label>
        <input type="text" id="editConcertTime" value="${concertTime}" placeholder="e.g., 19:30" style="width: 100%; padding: 10px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 14px; box-sizing: border-box;">
        <small style="color: #6b7280; display: block; margin-top: 4px;">Concert start time</small>
      </div>
      
      <div style="margin-bottom: 16px;">
        <label style="display: block; font-size: 14px; font-weight: 500; color: #374151; margin-bottom: 8px;">Venue</label>
        <input type="text" id="editConcertVenue" value="${concertVenue}" placeholder="e.g., Town Hall" style="width: 100%; padding: 10px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 14px; box-sizing: border-box;">
        <small style="color: #6b7280; display: block; margin-top: 4px;">Concert venue</small>
      </div>
      
      <div style="margin-bottom: 16px;">
        <label style="display: block; font-size: 14px; font-weight: 500; color: #374151; margin-bottom: 8px;">Uniform</label>
        <input type="text" id="editConcertUniform" value="${concertUniform}" placeholder="e.g., Black tie, Concert dress" style="width: 100%; padding: 10px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 14px; box-sizing: border-box;">
        <small style="color: #6b7280; display: block; margin-top: 4px;">Dress code / uniform requirements</small>
      </div>
      
      <div style="margin-bottom: 16px;">
        <label style="display: block; font-size: 14px; font-weight: 500; color: #374151; margin-bottom: 8px;">Programme</label>
        <div id="programmeSetsContainer" style="display: flex; flex-direction: column; gap: 12px;">
          <!-- Sets will be added here dynamically -->
        </div>
        <button type="button" onclick="addProgrammeSet()" class="btn secondary" style="padding: 6px 12px; margin-top: 8px; font-size: 13px;">+ Add Set</button>
        <small style="color: #6b7280; display: block; margin-top: 4px;">Add sets for the concert programme. Line breaks will be preserved.</small>
      </div>
      
      <div style="margin-bottom: 20px;">
        <label style="display: block; font-size: 14px; font-weight: 500; color: #374151; margin-bottom: 8px;">Other Info</label>
        <textarea id="editConcertOtherInfo" placeholder="Additional information..." style="width: 100%; padding: 10px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 14px; box-sizing: border-box; min-height: 80px;">${concertOtherInfo}</textarea>
        <small style="color: #6b7280; display: block; margin-top: 4px;">Any other relevant information</small>
      </div>
      
      <div style="display: flex; gap: 8px; justify-content: flex-end;">
        <button onclick="document.getElementById('rehearsalEditModalOverlay')?.remove()" class="btn secondary" style="padding: 8px 16px;">Cancel</button>
        <button onclick="saveConcertEditFromModal(${rehearsalNum})" class="btn" style="padding: 8px 16px;">Save Changes</button>
      </div>
    `;
  } else {
    // Original rehearsal/sectional modal
    modal.innerHTML = `
      <div style="display: flex; justify-content: space-between; align-items: start; margin-bottom: 20px;">
        <h3 style="margin: 0; font-size: 18px; font-weight: 600; color: #111827;">Edit Event ${rehearsalNum}</h3>
        <button onclick="document.getElementById('rehearsalEditModalOverlay')?.remove()" style="background: none; border: none; font-size: 24px; color: #6b7280; cursor: pointer; padding: 0; line-height: 1;">&times;</button>
      </div>
      
      <div style="margin-bottom: 16px;">
        <label style="display: block; font-size: 14px; font-weight: 500; color: #374151; margin-bottom: 8px;">Date</label>
        <input type="date" id="editDate" value="${date}" style="width: 100%; padding: 10px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 14px; box-sizing: border-box;">
        <small style="color: #6b7280; display: block; margin-top: 4px;">Date of the rehearsal</small>
      </div>
      
      <div style="margin-bottom: 16px;">
        <label style="display: block; font-size: 14px; font-weight: 500; color: #374151; margin-bottom: 8px;">Include in allocation</label>
        <select id="editIncludeInAllocation" style="width: 100%; padding: 10px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 14px; box-sizing: border-box;">
          <option value="Y" ${includeInAllocation === 'Y' ? 'selected' : ''}>Yes - Include in timing allocation</option>
          <option value="N" ${includeInAllocation === 'N' ? 'selected' : ''}>No - Exclude (sectional/concert)</option>
        </select>
        <small style="color: #6b7280; display: block; margin-top: 4px;">Whether to include in work allocation timing</small>
      </div>
      
      <div style="margin-bottom: 16px;">
        <label style="display: block; font-size: 14px; font-weight: 500; color: #374151; margin-bottom: 8px;">Event Type</label>
        <select id="editEventType" style="width: 100%; padding: 10px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 14px; box-sizing: border-box;">
          <option value="Rehearsal" ${eventType === 'Rehearsal' ? 'selected' : ''}>Rehearsal</option>
          <option value="Sectional" ${eventType === 'Sectional' ? 'selected' : ''}>Sectional</option>
          <option value="Concert" ${eventType === 'Concert' ? 'selected' : ''}>Concert</option>
          <option value="Contest" ${eventType === 'Contest' ? 'selected' : ''}>Contest</option>
        </select>
        <small style="color: #6b7280; display: block; margin-top: 4px;">Type of event (Rehearsal, Sectional, Concert, or Contest)</small>
      </div>
      
      <div style="margin-bottom: 16px;">
        <label style="display: block; font-size: 14px; font-weight: 500; color: #374151; margin-bottom: 8px;">Ensemble/Section</label>
        <input type="text" id="editSection" value="${section || 'Full Ensemble'}" placeholder="e.g., Cornets, Flutes, Full Ensemble" style="width: 100%; padding: 10px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 14px; box-sizing: border-box;">
        <small style="color: #6b7280; display: block; margin-top: 4px;">Which ensemble section performs this rehearsal</small>
      </div>
      
      <div id="workFieldContainer" style="margin-bottom: 16px; display: none;">
        <label style="display: block; font-size: 14px; font-weight: 500; color: #374151; margin-bottom: 8px;">Work</label>
        <input type="text" id="editWork" value="${rehearsalData?.work || ''}" placeholder="e.g., Nimrod, The President" style="width: 100%; padding: 10px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 14px; box-sizing: border-box;">
        <small style="color: #6b7280; display: block; margin-top: 4px;">Piece being rehearsed (for sectionals)</small>
      </div>
      
      <div style="margin-bottom: 16px;">
        <label style="display: block; font-size: 14px; font-weight: 500; color: #374151; margin-bottom: 8px;">Start Time (HH:MM)</label>
        <input type="text" id="editStartTime" value="${startTime}" placeholder="e.g., 14:00" style="width: 100%; padding: 10px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 14px; box-sizing: border-box;">
        <small style="color: #6b7280; display: block; margin-top: 4px;">Rehearsal start time</small>
      </div>
      
      <div style="margin-bottom: 20px;">
        <label style="display: block; font-size: 14px; font-weight: 500; color: #374151; margin-bottom: 8px;">End Time (HH:MM)</label>
        <input type="text" id="editEndTime" value="${endTime}" placeholder="e.g., 17:00" style="width: 100%; padding: 10px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 14px; box-sizing: border-box;">
        <small style="color: #6b7280; display: block; margin-top: 4px;">Rehearsal end time</small>
      </div>
      
      <div style="display: flex; gap: 8px; justify-content: flex-end;">
        <button onclick="document.getElementById('rehearsalEditModalOverlay')?.remove()" class="btn secondary" style="padding: 8px 16px;">Cancel</button>
        <button onclick="saveRehearsalEditFromModal(${rehearsalNum})" class="btn" style="padding: 8px 16px;">Save Changes</button>
      </div>
    `;
  }
  
  overlay.appendChild(modal);
  document.body.appendChild(overlay);
  
  // Add ESC key handler to close modal
  const handleEscape = (e) => {
    if (e.key === 'Escape') {
      overlay.remove();
    }
  };
  document.addEventListener('keydown', handleEscape);
  
  // Cleanup ESC handler when modal is removed
  const originalRemove = overlay.remove.bind(overlay);
  overlay.remove = () => {
    document.removeEventListener('keydown', handleEscape);
    originalRemove();
  };
  
  // Add Enter key handler
  const handleEnter = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      if (isConcert) {
        saveConcertEditFromModal(rehearsalNum);
      } else {
        saveRehearsalEditFromModal(rehearsalNum);
      }
    }
  };
  
  modal.addEventListener('keydown', handleEnter);
  
  // Add event listener to show/hide work field based on event type (non-concert only)
  if (!isConcert) {
    const eventTypeSelect = document.getElementById('editEventType');
    const workFieldContainer = document.getElementById('workFieldContainer');
    
    const updateWorkFieldVisibility = () => {
      const selectedType = eventTypeSelect.value;
      if (workFieldContainer) {
        workFieldContainer.style.display = selectedType === 'Sectional' ? 'block' : 'none';
      }
    };
    
    if (eventTypeSelect) {
      eventTypeSelect.addEventListener('change', updateWorkFieldVisibility);
      // Initialize visibility
      updateWorkFieldVisibility();
    }
  }
  
  // Initialize programme sets for concerts
  if (isConcert) {
    initializeProgrammeSets(concertProgramme);
  }
  
  // Focus appropriate input
  setTimeout(() => {
    const focusElement = isConcert ? document.getElementById('editConcertTitle') : document.getElementById('editDate');
    focusElement?.focus();
  }, 100);
}

/**
 * Initialize programme sets from saved programme text
 */
// Parse programme text into sets without trimming any lines
function parseProgrammeSets(programmeText) {
  const normalized = (programmeText || '').replace(/\r\n/g, '\n');
  const lines = normalized.split('\n');
  const setRegex = /^Set\s+(\d+):?/i;
  const sets = [];
  let currentNum = null;
  let currentLines = [];

  lines.forEach((line) => {
    const match = line.match(setRegex);
    if (match) {
      if (currentNum !== null) {
        sets.push({ num: currentNum, content: currentLines.join('\n') });
      }
      currentNum = match[1];
      currentLines = [];
    } else {
      // If no set header has been seen yet, assume Set 1
      if (currentNum === null) currentNum = 1;
      currentLines.push(line);
    }
  });

  if (currentNum !== null) {
    sets.push({ num: currentNum, content: currentLines.join('\n') });
  }

  // If nothing parsed, return a single set with the raw text
  if (sets.length === 0) {
    return [{ num: 1, content: normalized }];
  }

  return sets;
}

function initializeProgrammeSets(programmeText) {
  const container = document.getElementById('programmeSetsContainer');
  if (!container) return;

  console.log('[INIT SETS] Programme text:', programmeText);
  const parsedSets = parseProgrammeSets(programmeText);
  console.log('[INIT SETS] Parsed sets:', parsedSets.map(s => ({ num: s.num, len: s.content.length })));

  parsedSets.forEach((set, idx) => {
    addProgrammeSet(set.num || idx + 1, set.content);
  });
}

/**
 * Add a new programme set
 */
function addProgrammeSet(setNumber, content = '') {
  const container = document.getElementById('programmeSetsContainer');
  if (!container) return;
  
  // Determine set number if not provided
  if (!setNumber) {
    const existingSets = container.querySelectorAll('.programme-set');
    setNumber = existingSets.length + 1;
  }
  
  const setDiv = document.createElement('div');
  setDiv.className = 'programme-set';
  setDiv.style.cssText = 'border: 1px solid #d1d5db; border-radius: 6px; padding: 12px; background: #f9fafb;';
  
  setDiv.innerHTML = `
    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
      <label style="font-weight: 500; color: #374151; font-size: 13px;">Set ${setNumber}</label>
      <button type="button" onclick="removeProgrammeSet(this)" class="btn secondary" style="padding: 4px 8px; font-size: 12px; background: #ef4444; color: white;">Remove</button>
    </div>
    <textarea class="programme-set-content" placeholder="Enter pieces for this set, one per line..." style="width: 100%; padding: 8px; border: 1px solid #d1d5db; border-radius: 4px; font-size: 13px; box-sizing: border-box; min-height: 80px; font-family: monospace; resize: vertical;"></textarea>
  `;
  
  // Set textarea content after creating element to preserve special characters
  const textarea = setDiv.querySelector('.programme-set-content');
  if (textarea) {
    textarea.value = content;
  }
  
  container.appendChild(setDiv);
  
  // Renumber all sets
  renumberProgrammeSets();
}

/**
 * Remove a programme set
 */
function removeProgrammeSet(button) {
  const container = document.getElementById('programmeSetsContainer');
  if (!container) return;
  
  const setDiv = button.closest('.programme-set');
  if (setDiv) {
    setDiv.remove();
    renumberProgrammeSets();
    
    // If no sets left, add an empty one
    const remainingSets = container.querySelectorAll('.programme-set');
    if (remainingSets.length === 0) {
      addProgrammeSet(1, '');
    }
  }
}

/**
 * Renumber all programme sets
 */
function renumberProgrammeSets() {
  const container = document.getElementById('programmeSetsContainer');
  if (!container) return;
  
  const sets = container.querySelectorAll('.programme-set');
  sets.forEach((set, idx) => {
    const label = set.querySelector('label');
    if (label) {
      label.textContent = `Set ${idx + 1}`;
    }
  });
}

/**
 * Collect all programme sets into formatted text
 */
function collectProgrammeSets() {
  const container = document.getElementById('programmeSetsContainer');
  if (!container) return '';
  
  const sets = container.querySelectorAll('.programme-set');
  const parts = [];
  
  sets.forEach((set, idx) => {
    const textarea = set.querySelector('.programme-set-content');
    // Do not trim so we keep intentional blank lines
    const content = textarea?.value || '';
    if (content) {
      parts.push(`Set ${idx + 1}:\n${content}`);
    }
  });
  
  return parts.join('\n\n');
}

/**
 * Save rehearsal edits from the modal in editor.js context
 */
async function saveRehearsalEditFromModal(rehearsalNum) {
  const dateInput = document.getElementById('editDate');
  const startTimeInput = document.getElementById('editStartTime');
  const endTimeInput = document.getElementById('editEndTime');
  const includeInput = document.getElementById('editIncludeInAllocation');
  const eventTypeInput = document.getElementById('editEventType');
  const sectionInput = document.getElementById('editSection');
  const workInput = document.getElementById('editWork');
  
  if (!dateInput) return;
  
  const newDate = dateInput.value;
  const newStartTime = startTimeInput?.value.trim() || '';
  const newEndTime = endTimeInput?.value.trim() || '';
  const newIncludeInAllocation = includeInput?.value || 'Y';
  const newEvent = eventTypeInput?.value || 'Rehearsal';
  const newSection = sectionInput?.value.trim() || 'Full Ensemble';
  const newWork = workInput?.value.trim() || '';
  
  console.log("Save form values - Date:", newDate, "Start:", newStartTime, "End:", newEndTime, "Include:", newIncludeInAllocation, "Event:", newEvent, "Section:", newSection, "Work:", newWork);
  
  try {
    // Call API to update rehearsal details - apiBase already includes /api/s/{scheduleId}
    const url = `${apiBase()}/rehearsal/${rehearsalNum}`;
    console.log("Saving to URL:", url);
    
    const payloadBody = {
      date: newDate,
      start_time: newStartTime,
      end_time: newEndTime,
      include_in_allocation: newIncludeInAllocation,
      event: newEvent,
      section: newSection,
      work: newWork
    };
    console.log("Payload being sent:", JSON.parse(JSON.stringify(payloadBody)));
    
    const res = await fetch(url, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify(payloadBody)
    });
    
    if (!res.ok) {
      const errorText = await res.text();
      console.error("Error updating rehearsal:", errorText);
      alert("Failed to save rehearsal changes: " + errorText);
      return;
    }
    
    const result = await res.json();
    console.log("Rehearsal updated:", result);
    
    // Close modal - find and remove the overlay by ID
    const overlay = document.getElementById('rehearsalEditModalOverlay');
    if (overlay) overlay.remove();
    
    // Reload schedule data from server
    await refreshFromServer();
    
  } catch (e) {
    console.error("Error saving rehearsal:", e);
    alert("Failed to save rehearsal: " + e.message);
  }
}

/**
 * Save concert edits from the modal in editor.js context
 */
async function saveConcertEditFromModal(rehearsalNum) {
  const titleInput = document.getElementById('editConcertTitle');
  const dateInput = document.getElementById('editConcertDate');
  const timeInput = document.getElementById('editConcertTime');
  const venueInput = document.getElementById('editConcertVenue');
  const uniformInput = document.getElementById('editConcertUniform');
  const programmeInput = document.getElementById('editConcertProgramme');
  const otherInfoInput = document.getElementById('editConcertOtherInfo');
  
  if (!titleInput || !dateInput) return;
  
  const title = titleInput.value.trim() || 'Concert';
  const date = dateInput.value;
  const time = timeInput?.value.trim() || '';
  const venue = venueInput?.value.trim() || '';
  const uniform = uniformInput?.value.trim() || '';
  const programme = collectProgrammeSets();
  const otherInfo = otherInfoInput?.value.trim() || '';
  
  try {
    // Find the concert_id from STATE.rehearsals (more reliable than timed data)
    let concertId = null;
    console.log('[CONCERT EDIT] Looking for concert_id for rehearsal', rehearsalNum);
    
    // First try to find concert_id from rehearsals table
    if (STATE.rehearsals && Array.isArray(STATE.rehearsals)) {
      const rehearsalRow = STATE.rehearsals.find(r => parseInt(r.Rehearsal) === rehearsalNum);
      console.log('[CONCERT EDIT] Found rehearsal row:', rehearsalRow);
      
      if (rehearsalRow) {
        concertId = rehearsalRow.concert_id;
        console.log('[CONCERT EDIT] concert_id from rehearsal row:', concertId);
      }
    }
    
    // Fallback: try to find from timed data
    if (!concertId && STATE.timed && Array.isArray(STATE.timed)) {
      const timedRow = STATE.timed.find(t => parseInt(t.Rehearsal) === rehearsalNum);
      if (timedRow && timedRow.concert_id) {
        concertId = timedRow.concert_id;
        console.log('[CONCERT EDIT] concert_id from timed row:', concertId);
      }
    }
    
    // Final fallback: try to find from concerts array by matching rehearsal number date
    if (!concertId && STATE.concerts && Array.isArray(STATE.concerts)) {
      const rehearsalRow = STATE.rehearsals?.find(r => parseInt(r.Rehearsal) === rehearsalNum);
      if (rehearsalRow) {
        const rehearsalDate = rehearsalRow.Date;
        const concert = STATE.concerts.find(c => c.date === rehearsalDate);
        if (concert) {
          concertId = concert.id;
          console.log('[CONCERT EDIT] concert_id from concerts array:', concertId);
        }
      }
    }
    
    console.log('[CONCERT EDIT] Final concert_id:', concertId);
    
    if (!concertId) {
      alert("Concert ID not found. Please ensure this is a concert event and try refreshing the page.");
      console.error('[CONCERT EDIT] Concert ID not found. STATE:', { rehearsals: STATE.rehearsals, timed: STATE.timed, concerts: STATE.concerts });
      return;
    }
    
    // Call API to update concert details using schedule-specific endpoint
    // apiBase() already includes /api/s/<schedule_id>[?token]; just append concert path
    const url = `${apiBase()}/concert/${concertId}`;
    
    const payloadBody = {
      title: title,
      date: date,
      time: time,
      venue: venue,
      uniform: uniform,
      programme: programme,
      other_info: otherInfo
    };
    
    console.log('[CONCERT EDIT] Updating concert via:', url, 'with data:', payloadBody);
    
    const res = await fetch(url, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify(payloadBody)
    });
    
    if (!res.ok) {
      const errorText = await res.text();
      console.error("Error updating concert:", errorText);
      alert("Failed to save concert changes: " + errorText);
      return;
    }
    
    const result = await res.json();
    console.log("Concert updated:", result);
    
    // Close modal
    const overlay = document.getElementById('rehearsalEditModalOverlay');
    if (overlay) overlay.remove();
    
    // Reload schedule data from server to get updated values
    await refreshFromServer();
    
    // Regenerate schedule to update timeline positions
    console.log('[CONCERT EDIT] Regenerating schedule to update timeline...');
    await generateSchedule();
    
    alert('Concert updated successfully! Timeline has been refreshed.');
    
  } catch (e) {
    console.error("Error saving concert:", e);
    alert("Failed to save concert: " + e.message);
  }
}

/**
 * Open modal to add a new rehearsal with Date, Start Time, End Time, Section
 */
function openAddEventModal() {
  // Create modal overlay
  const overlay = document.createElement("div");
  overlay.id = "addRehearsalModalOverlay";
  overlay.style.cssText = `
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    bottom: 0;
    background: rgba(0,0,0,0.5);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 10000;
  `;
  
  // Create modal content
  const modal = document.createElement("div");
  modal.id = "addEventModal";
  modal.style.cssText = `
    background: white;
    border-radius: 8px;
    padding: 24px;
    max-width: 500px;
    width: 90%;
    max-height: 80vh;
    overflow-y: auto;
    box-shadow: 0 20px 25px -5px rgba(0,0,0,0.1), 0 10px 10px -5px rgba(0,0,0,0.04);
  `;
  
  // Calculate next event number
  const nextNum = Math.max(0, ...STATE.rehearsals.map(r => parseInt(r.Rehearsal || 0))) + 1;
  
  // Get tomorrow's date as default
  const tomorrow = new Date();
  tomorrow.setDate(tomorrow.getDate() + 1);
  const defaultDate = tomorrow.toISOString().substring(0, 10);
  
  modal.innerHTML = `
    <div style="display: flex; justify-content: space-between; align-items: start; margin-bottom: 20px;">
      <h3 style="margin: 0; font-size: 18px; font-weight: 600; color: #111827;">Add New Event</h3>
      <button onclick="document.getElementById('addRehearsalModalOverlay')?.remove()" style="background: none; border: none; font-size: 24px; color: #6b7280; cursor: pointer; padding: 0; line-height: 1;">&times;</button>
    </div>
    
    <div style="margin-bottom: 20px;">
      <label style="display: block; font-size: 14px; font-weight: 500; color: #374151; margin-bottom: 8px;">Event Type</label>
      <select id="addEventType" onchange="updateEventFormFields()" style="width: 100%; padding: 10px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 14px; box-sizing: border-box;">
        <option value="Rehearsal" selected>Rehearsal</option>
        <option value="Concert">Concert</option>
        <option value="Sectional">Sectional</option>
      </select>
      <small style="color: #6b7280; display: block; margin-top: 4px;">Select the type of event</small>
    </div>

    <div id="eventFormFields">
      <!-- Fields will be dynamically inserted here -->
    </div>
    
    <div style="display: flex; gap: 8px; justify-content: flex-end; margin-top: 20px;">
      <button onclick="document.getElementById('addRehearsalModalOverlay')?.remove()" class="btn secondary" style="padding: 8px 16px;">Cancel</button>
      <button onclick="(async () => await saveAddEventFromModal())()" class="btn" style="padding: 8px 16px;"><span id="createButtonText">Create Rehearsal</span></button>
    </div>
  `;
  
  overlay.appendChild(modal);
  document.body.appendChild(overlay);
  
  // Initial form fields for Rehearsal
  updateEventFormFields();
  
  // Add Enter key handler
  modal.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      saveAddEventFromModal();
    }
  });
}

function updateEventFormFields() {
  const eventType = document.getElementById('addEventType')?.value || 'Rehearsal';
  const fieldsContainer = document.getElementById('eventFormFields');
  const buttonText = document.getElementById('createButtonText');
  
  if (!fieldsContainer) return;
  
  // Calculate next event number
  const nextNum = Math.max(0, ...STATE.rehearsals.map(r => parseInt(r.Rehearsal || 0))) + 1;
  
  // Get tomorrow's date as default
  const tomorrow = new Date();
  tomorrow.setDate(tomorrow.getDate() + 1);
  const defaultDate = tomorrow.toISOString().substring(0, 10);
  
  // Update button text
  if (buttonText) {
    buttonText.textContent = `Create ${eventType}`;
  }
  
  // Common fields for all types
  let html = `
    <div style="margin-bottom: 16px;">
      <label style="display: block; font-size: 14px; font-weight: 500; color: #374151; margin-bottom: 8px;">Number</label>
      <input type="number" id="addEventNumber" value="${nextNum}" min="1" style="width: 100%; padding: 10px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 14px; box-sizing: border-box;">
      <small style="color: #6b7280; display: block; margin-top: 4px;">Sequential number (auto-calculated)</small>
    </div>
    
    <div style="margin-bottom: 16px;">
      <label style="display: block; font-size: 14px; font-weight: 500; color: #374151; margin-bottom: 8px;">Date</label>
      <input type="date" id="addEventDate" value="${defaultDate}" style="width: 100%; padding: 10px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 14px; box-sizing: border-box;">
      <small style="color: #6b7280; display: block; margin-top: 4px;">Date of the ${eventType.toLowerCase()}</small>
    </div>
  `;
  
  if (eventType === 'Rehearsal') {
    html += `
      <div style="margin-bottom: 16px;">
        <label style="display: block; font-size: 14px; font-weight: 500; color: #374151; margin-bottom: 8px;">Start Time (HH:MM)</label>
        <input type="text" id="addEventStartTime" value="14:00" placeholder="e.g., 14:00" style="width: 100%; padding: 10px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 14px; box-sizing: border-box;">
      </div>
      
      <div style="margin-bottom: 16px;">
        <label style="display: block; font-size: 14px; font-weight: 500; color: #374151; margin-bottom: 8px;">End Time (HH:MM)</label>
        <input type="text" id="addEventEndTime" value="17:00" placeholder="e.g., 17:00" style="width: 100%; padding: 10px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 14px; box-sizing: border-box;">
      </div>
      
      <div style="margin-bottom: 16px;">
        <label style="display: block; font-size: 14px; font-weight: 500; color: #374151; margin-bottom: 8px;">Section</label>
        <input type="text" id="addEventSection" value="Full Ensemble" placeholder="e.g., Full Ensemble" style="width: 100%; padding: 10px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 14px; box-sizing: border-box;">
      </div>
    `;
  } else if (eventType === 'Concert') {
    html += `
      <div style="margin-bottom: 16px;">
        <label style="display: block; font-size: 14px; font-weight: 500; color: #374151; margin-bottom: 8px;">Concert Time (HH:MM)</label>
        <input type="text" id="addEventTime" value="19:00" placeholder="e.g., 19:00" style="width: 100%; padding: 10px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 14px; box-sizing: border-box;">
      </div>
      
      <div style="margin-bottom: 16px;">
        <label style="display: block; font-size: 14px; font-weight: 500; color: #374151; margin-bottom: 8px;">Venue</label>
        <input type="text" id="addEventVenue" placeholder="e.g., Royal Albert Hall" style="width: 100%; padding: 10px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 14px; box-sizing: border-box;">
      </div>
      
      <div style="margin-bottom: 16px;">
        <label style="display: block; font-size: 14px; font-weight: 500; color: #374151; margin-bottom: 8px;">Uniform</label>
        <input type="text" id="addEventUniform" placeholder="e.g., Black Tie" style="width: 100%; padding: 10px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 14px; box-sizing: border-box;">
      </div>
    `;
  } else if (eventType === 'Sectional') {
    html += `
      <div style="margin-bottom: 16px;">
        <label style="display: block; font-size: 14px; font-weight: 500; color: #374151; margin-bottom: 8px;">Start Time (HH:MM)</label>
        <input type="text" id="addEventStartTime" value="14:00" placeholder="e.g., 14:00" style="width: 100%; padding: 10px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 14px; box-sizing: border-box;">
      </div>
      
      <div style="margin-bottom: 16px;">
        <label style="display: block; font-size: 14px; font-weight: 500; color: #374151; margin-bottom: 8px;">End Time (HH:MM)</label>
        <input type="text" id="addEventEndTime" value="17:00" placeholder="e.g., 17:00" style="width: 100%; padding: 10px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 14px; box-sizing: border-box;">
      </div>
      
      <div style="margin-bottom: 16px;">
        <label style="display: block; font-size: 14px; font-weight: 500; color: #374151; margin-bottom: 8px;">Section</label>
        <input type="text" id="addEventSection" value="" placeholder="e.g., Violin 1, Woodwinds" style="width: 100%; padding: 10px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 14px; box-sizing: border-box;">
        <small style="color: #6b7280; display: block; margin-top: 4px;">Which section this is for</small>
      </div>
    `;
  }
  
  fieldsContainer.innerHTML = html;
  
  // Focus the date input
  setTimeout(() => document.getElementById('addEventDate')?.focus(), 100);
}

function openAddRehearsalModal() {
  openAddEventModal();
}

/**
 * Save new event from the add modal
 */
async function saveAddEventFromModal() {
  const eventType = document.getElementById('addEventType')?.value || 'Rehearsal';
  const numInput = document.getElementById('addEventNumber');
  const dateInput = document.getElementById('addEventDate');
  
  if (!numInput || !dateInput) {
    alert("Please fill in all required fields");
    return;
  }
  
  const eventNum = numInput.value.trim();
  const date = dateInput.value;
  
  // Validate inputs
  if (!eventNum || !date) {
    alert("Please fill in Number and Date");
    return;
  }
  
  // Get type-specific fields
  let startTime = '';
  let endTime = '';
  let section = 'Full Ensemble';
  let includeInAllocation = 'N'; // Concerts and Sectionals excluded by default
  let eventField = eventType; // For the Event column
  
  if (eventType === 'Rehearsal') {
    startTime = document.getElementById('addEventStartTime')?.value.trim() || '';
    endTime = document.getElementById('addEventEndTime')?.value.trim() || '';
    section = document.getElementById('addEventSection')?.value.trim() || 'Full Ensemble';
    includeInAllocation = 'Y'; // Rehearsals included by default
    eventField = ''; // Regular rehearsals don't have an Event value
  } else if (eventType === 'Concert') {
    const time = document.getElementById('addEventTime')?.value.trim() || '';
    startTime = time;
    endTime = time;
    section = 'Full Ensemble';
    eventField = 'Concert';
  } else if (eventType === 'Sectional') {
    startTime = document.getElementById('addEventStartTime')?.value.trim() || '';
    endTime = document.getElementById('addEventEndTime')?.value.trim() || '';
    section = document.getElementById('addEventSection')?.value.trim() || '';
    eventField = 'Sectional';
  }
  
  // Create new row matching rehearsals_cols structure
  const newRow = {};
  STATE.rehearsals_cols.forEach(c => {
    if (c === 'Rehearsal') newRow[c] = rehNum;
    else if (c === 'Date') newRow[c] = date;
    else if (c === 'Start Time') newRow[c] = startTime;
    else if (c === 'End Time') newRow[c] = endTime;
    else if (c === 'Section') newRow[c] = section;
    else if (c === 'Include in allocation') newRow[c] = includeInAllocation;
    else if (c === 'Event' || c === 'Event Type') newRow[c] = eventField;
    else newRow[c] = '';
  });
  
  // Add to STATE
  STATE.rehearsals.push(newRow);
  
  // Sort and renumber chronologically
  sortAndRenumberRehearsals();
  
  // Save to server
  await saveInputs();
  
  // Close modal
  const overlay = document.getElementById('addRehearsalModalOverlay');
  if (overlay) overlay.remove();
  
  // Re-render rehearsals table
  renderTable("rehearsals-table", STATE.rehearsals, STATE.rehearsals_cols, saveInputs);
  
  console.log(`New ${eventType} added:`, newRow);
}

/**
 * Save new rehearsal from the add modal (backward compatibility)
 */
async function saveAddRehearsalFromModal() {
  saveAddEventFromModal();
}

function addWorkToTimeline(work, rehearsalNum, duration, position) {
  const beforeState = deepCopy(STATE.timed);
  
  const bounds = getRehearsalBounds(rehearsalNum);
  if (!bounds) {
    alert("Rehearsal not found");
    return;
  }
  
  // Convert current state to timeline format
  let timelineItems = timedToTimelineItems(STATE.timed);
  
  // Get items for this rehearsal
  const rehearsalItems = timelineItems.filter(it => it.rehearsalNum === rehearsalNum);
  const otherItems = timelineItems.filter(it => it.rehearsalNum !== rehearsalNum);
  
  // Check if we have space
  const currentDuration = rehearsalItems.reduce((sum, item) => sum + item.duration_mins, 0);
  const availableSpace = bounds.totalMins - currentDuration;
  
  if (duration > availableSpace) {
    // Need to shrink existing works to make space
    const deficit = duration - availableSpace;
    
    // Distribute the deficit proportionally across existing works
    let remainingDeficit = deficit;
    rehearsalItems.forEach(item => {
      if (remainingDeficit <= 0) return;
      
      // Calculate proportional reduction (but keep minimum 5 mins)
      const maxReduction = Math.max(0, item.duration_mins - TIMELINE_CONFIG.minDuration);
      const reduction = Math.min(remainingDeficit, maxReduction);
      
      item.duration_mins -= reduction;
      remainingDeficit -= reduction;
    });
    
    if (remainingDeficit > 0) {
      alert(`Cannot insert work: Need ${remainingDeficit} more minutes. Try removing other works or reducing their durations.`);
      return;
    }
  }
  
  // Determine insertion point
  let insertIndex = 0;
  if (position === 'start') {
    insertIndex = 0;
  } else {
    insertIndex = rehearsalItems.length;
  }
  
  // Create new item
  const rehearsalDate = STATE.rehearsals.find(r => parseInt(r.Rehearsal) === rehearsalNum)?.Date || '';
  const newItem = {
    id: `temp-${Date.now()}`,
    title: work.Title,
    start: bounds.startTime,
    start_time: bounds.startTime,
    duration_mins: duration,
    rehearsalNum: rehearsalNum,
    rehearsal: rehearsalNum,
    originalIndex: -1,
    date: rehearsalDate,
    breakStart: '',
    breakEnd: '',
  };
  
  // Insert into rehearsal items
  rehearsalItems.splice(insertIndex, 0, newItem);
  
  // Compact to recalculate start times
  let currentTime = bounds.startTime;
  rehearsalItems.forEach(item => {
    item.start = currentTime;
    item.start_time = currentTime;
    currentTime = addMinutes(currentTime, item.duration_mins);
  });
  
  // Merge back with other rehearsals
  timelineItems = [...otherItems, ...rehearsalItems];
  
  // Convert back to timed format
  STATE.timed = timelineItemsToTimed(timelineItems);
  
  // Re-index ALL items properly (important for new items with originalIndex = -1)
  STATE.timed = STATE.timed.map((t, idx) => ({ ...t, _index: idx }));
  
  // Ensure all items have the full set of required fields from STATE
  STATE.timed.forEach((entry, idx) => {
    // If Date is missing, try to get it from rehearsal
    if (!entry.Date) {
      const reh = STATE.rehearsals.find(r => parseInt(r.Rehearsal) === parseInt(entry.Rehearsal));
      if (reh) entry.Date = reh.Date || '';
    }
  });
  
  const afterState = deepCopy(STATE.timed);
  
  // Push to history
  pushToHistory("add", beforeState, afterState);
  
  // Save and re-render
  saveTimelineToBackend();
  renderTimelineEditor();
  
  // Update allocation and schedule views
  refreshComputedTables();
}

function refreshComputedTables() {
  // Re-run allocation and schedule generation on the backend
  apiPost(`/generate_schedule`, {}).then(() => {
    // Refresh just the allocation and schedule tables without reloading timeline
    apiGet(`/state`).then(data => {
      STATE.allocation = data.allocation || [];
      STATE.schedule = data.schedule || [];
      
      // Re-render allocation table (use original if available)
      if (STATE.allocation_original && STATE.allocation_original.length > 0) {
        const allocCols = Object.keys(STATE.allocation_original[0]);
        renderOutputTable("allocation-table", STATE.allocation_original, allocCols);
        
        // Ensure allocation accordion is collapsed (but clickable)
        requestAnimationFrame(() => {
          const allHeaders = document.querySelectorAll('.accordion-header');
          allHeaders.forEach(header => {
            const headerText = header.textContent.trim();
            if (headerText.includes("Allocation")) {
              // Make sure it's collapsed
              if (!header.classList.contains("collapsed")) {
                header.classList.add("collapsed");
              }
              // Make sure content is hidden
              if (header.nextElementSibling) {
                if (!header.nextElementSibling.classList.contains("hidden")) {
                  header.nextElementSibling.classList.add("hidden");
                }
              }
            }
          });
        });
      }
      
      // Re-render schedule comparison
      if (STATE.schedule && STATE.schedule.length > 0) {
        renderScheduleComparison("schedule-table", STATE.schedule_original, STATE.schedule);
        
        // Ensure schedule accordion is collapsed (but clickable)
        requestAnimationFrame(() => {
          const allHeaders = document.querySelectorAll('.accordion-header');
          allHeaders.forEach(header => {
            const headerText = header.textContent.trim();
            if (headerText.includes("Schedule")) {
              // Make sure it's collapsed
              if (!header.classList.contains("collapsed")) {
                header.classList.add("collapsed");
              }
              // Make sure content is hidden
              if (header.nextElementSibling) {
                if (!header.nextElementSibling.classList.contains("hidden")) {
                  header.nextElementSibling.classList.add("hidden");
                }
              }
            }
          });
        });
      }
    });
  }).catch(err => {
    console.error("Failed to refresh computed tables:", err);
  });
}

// ========================
// Additional Functions
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

// --- DELETE REHEARSAL FUNCTIONS ---

/**
 * Delete a rehearsal, checking for associated concerts first
 */
async function deleteRehearsal(rehearsalNum) {
  try {
    // First, check if there are concerts associated with this rehearsal
    const url = `${apiBase()}/rehearsal/${rehearsalNum}/concerts`;
    const res = await fetch(url, {
      method: "GET",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin"
    });
    
    if (!res.ok) {
      throw new Error("Failed to fetch concert data");
    }
    
    const data = await res.json();
    const concerts = data.concerts || [];
    
    if (concerts.length > 0) {
      // Show modal to ask about concert deletion
      showConcertSelectionModal(rehearsalNum, concerts);
    } else {
      // No concerts, just confirm deletion
      if (confirm(`Delete Rehearsal ${rehearsalNum}? This cannot be undone.`)) {
        await performRehearsalDeletion(rehearsalNum, []);
      }
    }
  } catch (err) {
    console.error("Error checking for concerts:", err);
    alert("Error: " + err.message);
  }
}

/**
 * Show modal to select which concerts to delete along with the rehearsal
 */
function showConcertSelectionModal(rehearsalNum, concerts) {
  const overlay = document.createElement("div");
  overlay.id = "concertSelectionModalOverlay";
  overlay.style.cssText = `
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    bottom: 0;
    background: rgba(0,0,0,0.5);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 10000;
  `;
  
  const modal = document.createElement("div");
  modal.style.cssText = `
    background: white;
    border-radius: 8px;
    padding: 24px;
    max-width: 500px;
    width: 90%;
    box-shadow: 0 20px 25px -5px rgba(0,0,0,0.1);
  `;
  
  const concertCheckboxes = concerts.map(c => `
    <div style="padding: 8px 0;">
      <label style="display: flex; align-items: center; gap: 8px; cursor: pointer;">
        <input type="checkbox" value="${c.id}" class="concert-checkbox" checked style="width: 18px; height: 18px; cursor: pointer;">
        <span style="font-size: 14px;">${c.title} (${c.date})</span>
      </label>
    </div>
  `).join('');
  
  modal.innerHTML = `
    <h3 style="margin: 0 0 16px 0; font-size: 18px; font-weight: 600;">Delete Rehearsal ${rehearsalNum}</h3>
    <p style="margin: 0 0 16px 0; color: #6b7280; font-size: 14px;">
      This rehearsal has ${concerts.length} associated concert(s). Do you want to delete them as well?
    </p>
    <div style="border: 1px solid #e5e7eb; border-radius: 6px; padding: 12px; margin-bottom: 20px; max-height: 200px; overflow-y: auto;">
      ${concertCheckboxes}
    </div>
    <div style="display: flex; gap: 8px; justify-content: flex-end;">
      <button onclick="document.getElementById('concertSelectionModalOverlay')?.remove()" class="btn secondary" style="padding: 8px 16px;">Cancel</button>
      <button id="confirmDeleteWithConcerts" class="btn" style="padding: 8px 16px; background: #dc3545;">Delete</button>
    </div>
  `;
  
  overlay.appendChild(modal);
  document.body.appendChild(overlay);
  
  document.getElementById('confirmDeleteWithConcerts').addEventListener('click', async () => {
    const checkboxes = modal.querySelectorAll('.concert-checkbox:checked');
    const concertIds = Array.from(checkboxes).map(cb => cb.value);
    
    overlay.remove();
    await performRehearsalDeletion(rehearsalNum, concertIds);
  });
}

/**
 * Actually delete the rehearsal and selected concerts
 */
async function performRehearsalDeletion(rehearsalNum, concertIds) {
  try {
    const url = `${apiBase()}/rehearsal/${rehearsalNum}`;
    const res = await fetch(url, {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ concert_ids: concertIds })
    });
    
    if (!res.ok) {
      throw new Error(await res.text());
    }
    
    // Refresh the page to show updated data
    await refreshFromServer();
    alert(`✓ Rehearsal ${rehearsalNum} deleted successfully`);
  } catch (err) {
    console.error("Error deleting rehearsal:", err);
    alert("Error deleting rehearsal: " + err.message);
  }
}

// --- GLOBAL EXPORTS ---
window.initEditor = initEditor;
window.uploadXlsx = uploadXlsx;
window.addWorkRow = addWorkRow;
window.addRehearsalRow = addRehearsalRow;
window.addEventRow = addEventRow;
window.updateEventFormFields = updateEventFormFields;
window.saveAddEventFromModal = saveAddEventFromModal;
window.runAllocation = runAllocation;
window.generateSchedule = generateSchedule;
window.saveInputs = saveInputs;
window.refreshFromServer = refreshFromServer;
window.renderTimelineEditor = renderTimelineEditor;
window.renderScheduleAccordion = renderScheduleAccordion;
window.renderTimedAccordion = renderTimedAccordion;
window.toggleHistoryPanel = toggleHistoryPanel;
window.toggleAccordion = toggleAccordion;
window.revertToHistory = revertToHistory;
window.timelineUndo = timelineUndo;
window.timelineRedo = timelineRedo;
window.deleteRehearsal = deleteRehearsal;
window.revertToOriginal = revertToOriginal;

