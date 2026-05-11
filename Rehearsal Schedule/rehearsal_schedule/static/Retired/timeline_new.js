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



// ========================
// NEW TIMELINE EDITOR 
// ========================

/**
 * Render timeline editor with drag-to-move and resize functionality
 */
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

    // Sort rehearsals chronologically (fallback to rehearsal number)
    const sortedRehearsals = Object.keys(byRehearsal)
      .map(rnum => {
        const rehRow = STATE.rehearsals?.find(r => parseInt(r.Rehearsal) === parseInt(rnum));
        const dateStr = rehRow?.Date || byRehearsal[rnum][0]?.Date || "";
        const startTime = rehRow?.['Start Time'] || byRehearsal[rnum][0]?.['Time in Rehearsal'] || "00:00:00";
        const normalized = `${dateStr}`.includes("T") ? `${dateStr}` : `${dateStr} ${startTime}`;
        const sortTs = Date.parse(normalized.replace(" ", "T"));
        return {
          rnum: parseInt(rnum),
          sortTs: isNaN(sortTs) ? Number.POSITIVE_INFINITY : sortTs,
        };
      })
      .sort((a, b) => {
        if (a.sortTs !== b.sortTs) return a.sortTs - b.sortTs;
        return a.rnum - b.rnum;
      });

    sortedRehearsals.forEach(({ rnum }) => {
      const column = renderRehearsalColumn(rnum, byRehearsal[rnum]);
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

  // Header with edit button
  const header = document.createElement("div");
  
  // Determine event type and color
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
  
  // Normalize event type (case-insensitive)
  const eventTypeNormalized = eventType.trim();
  
  // Set colors based on event type
  switch (eventTypeNormalized) {
    case 'Concert':
    case 'Contest':
      headerBgColor = "#dbeafe"; // Light blue
      headerBorderColor = "#0284c7"; // Dark blue
      headerTextColor = "#0c4a6e"; // Very dark blue
      break;
    case 'Sectional':
      headerBgColor = "#fef3c7"; // Light amber
      headerBorderColor = "#f59e0b"; // Amber
      headerTextColor = "#78350f"; // Dark amber
      break;
    default: // Rehearsal
      headerBgColor = "#f3f4f6"; // Light gray
      headerBorderColor = "#d1d5db"; // Gray
      headerTextColor = "#000";
  }
  
  header.style.cssText = `padding:12px; background:${headerBgColor}; border-bottom:2px solid ${headerBorderColor}; border-left:4px solid ${headerBorderColor}; display:flex; align-items:center; gap:8px; flex-wrap:nowrap; cursor:pointer;`;
  
  // Different behavior for concerts/contests vs other events
  if (eventTypeNormalized === 'Concert' || eventTypeNormalized === 'Contest') {
    header.ondblclick = () => {
      // Get concert_id and redirect to admin page
      const rehearsalRow = STATE.rehearsals?.find(r => parseInt(r.Rehearsal) === rehearsalNum);
      const concertId = rehearsalRow?.concert_id;
      if (concertId && STATE.ensemble_id) {
        window.open(`/admin/ensembles/${STATE.ensemble_id}/concerts/${concertId}/edit`, '_blank');
      } else if (STATE.ensemble_id) {
        window.open(`/admin/ensembles/${STATE.ensemble_id}/concerts`, '_blank');
      } else {
        alert('Cannot edit concert: missing concert ID');
      }
    };
    header.title = "Double-click to edit concert details (opens admin page)";
  } else {
    header.ondblclick = () => openRehearsalEditModal(rehearsalNum);
    header.title = "Double-click to edit event details";
  }
  
  const headerTitle = document.createElement("div");
  headerTitle.style.cssText = `font-weight:600; text-align:center; flex:1; min-width:0; color:${headerTextColor};`;
  headerTitle.innerHTML = `${eventTypeNormalized} ${rehearsalNum}`;
  header.appendChild(headerTitle);
  
  const editBtn = document.createElement("button");
  editBtn.textContent = (eventTypeNormalized === 'Concert' || eventTypeNormalized === 'Contest') ? '🎵 Edit Concert' : '✎ Edit';
  editBtn.style.cssText = "background:#3b82f6; color:white; border:none; border-radius:4px; padding:6px 12px; cursor:pointer; font-size:12px; font-weight:bold; flex-shrink:0; white-space:nowrap;";
  
  if (eventTypeNormalized === 'Concert' || eventTypeNormalized === 'Contest') {
    editBtn.onclick = () => {
      const rehearsalRow = STATE.rehearsals?.find(r => parseInt(r.Rehearsal) === rehearsalNum);
      const concertId = rehearsalRow?.concert_id;
      if (concertId && STATE.ensemble_id) {
        window.open(`/admin/ensembles/${STATE.ensemble_id}/concerts/${concertId}/edit`, '_blank');
      } else if (STATE.ensemble_id) {
        window.open(`/admin/ensembles/${STATE.ensemble_id}/concerts`, '_blank');
      } else {
        alert('Cannot edit concert: missing concert ID');
      }
    };
  } else {
    editBtn.onclick = () => openRehearsalEditModal(rehearsalNum);
  }
  
  header.appendChild(editBtn);
  
  // Add delete button
  const deleteBtn = document.createElement("button");
  deleteBtn.textContent = '🗑️';
  deleteBtn.style.cssText = "background:#dc3545; color:white; border:none; border-radius:4px; padding:6px 10px; cursor:pointer; font-size:14px; font-weight:bold; flex-shrink:0;";
  deleteBtn.title = "Delete this rehearsal";
  deleteBtn.onclick = (e) => {
    e.stopPropagation();
    deleteRehearsal(rehearsalNum);
  };
  header.appendChild(deleteBtn);
  
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

/**
 * Update edit modal fields based on selected event type
 */
function updateEditModalFields() {
  const typeSelect = document.getElementById('rehearsalEventType');
  if (!typeSelect) return;
  
  const eventType = typeSelect.value;
  
  // Get all field containers
  const startTimeField = document.getElementById('rehearsalStartTime')?.parentElement;
  const endTimeField = document.getElementById('rehearsalEndTime')?.parentElement;
  const sectionField = document.getElementById('rehearsalSection')?.parentElement;
  const workField = document.getElementById('sectionalWorkField');
  const includeField = document.getElementById('rehearsalIncludeInAllocation')?.parentElement;
  const venueField = document.getElementById('rehearsalVenue')?.parentElement;
  const uniformField = document.getElementById('rehearsalUniform')?.parentElement;
  const timeField = document.getElementById('rehearsalTime')?.parentElement;
  
  // Get the include in allocation select
  const includeSelect = document.getElementById('rehearsalIncludeInAllocation');
  
  // Show/hide fields based on event type
  if (eventType === 'Rehearsal') {
    // Rehearsal: show start/end time, section, include in allocation
    if (startTimeField) startTimeField.style.display = 'block';
    if (endTimeField) endTimeField.style.display = 'block';
    if (sectionField) sectionField.style.display = 'block';
    if (workField) workField.style.display = 'none';
    if (includeField) includeField.style.display = 'block';
    if (venueField) venueField.style.display = 'none';
    if (uniformField) uniformField.style.display = 'none';
    if (timeField) timeField.style.display = 'none';
    
    // Auto-set include in allocation to Yes for rehearsals
    if (includeSelect) includeSelect.value = 'Yes - Include';
  } else if (eventType === 'Concert') {
    // Concert: show time, venue, uniform; hide start/end, section, include
    if (startTimeField) startTimeField.style.display = 'none';
    if (endTimeField) endTimeField.style.display = 'none';
    if (sectionField) sectionField.style.display = 'none';
    if (workField) workField.style.display = 'none';
    if (includeField) includeField.style.display = 'none';
    if (venueField) venueField.style.display = 'block';
    if (uniformField) uniformField.style.display = 'block';
    if (timeField) timeField.style.display = 'block';
    
    // Auto-set include in allocation to No for concerts
    if (includeSelect) includeSelect.value = 'No - Exclude (sectional/concert)';
  } else if (eventType === 'Sectional') {
    // Sectional: show start/end time, section, work; hide include, venue, uniform
    if (startTimeField) startTimeField.style.display = 'block';
    if (endTimeField) endTimeField.style.display = 'block';
    if (sectionField) sectionField.style.display = 'block';
    if (workField) workField.style.display = 'block';
    if (includeField) includeField.style.display = 'none';
    if (venueField) venueField.style.display = 'none';
    if (uniformField) uniformField.style.display = 'none';
    if (timeField) timeField.style.display = 'none';
    
    // Auto-set include in allocation to No for sectionals
    if (includeSelect) includeSelect.value = 'No - Exclude (sectional/concert)';
  }
}

/**
 * Open modal to edit event (rehearsal, concert, sectional) details
 */
function openRehearsalEditModal(rehearsalNum) {
  // Find the rehearsal data from both timed and rehearsals
  const rehearsalRows = STATE.timed.filter(row => parseInt(row.Rehearsal) === rehearsalNum);
  if (rehearsalRows.length === 0) return;
  
  const timedRow = rehearsalRows[0];
  const date = timedRow.Date || '';
  const section = timedRow.Section || 'Full Ensemble';
  const work = timedRow.Work || timedRow.Title || '';
  
  // Try to find rehearsal data from STATE.rehearsals if available
  let startTime = '';
  let endTime = '';
  let eventType = 'Rehearsal';
  let includeInAllocation = 'Yes - Include';
  let venue = '';
  let uniform = '';
  let time = '';
  
  if (STATE.rehearsals && Array.isArray(STATE.rehearsals)) {
    const rehearsalData = STATE.rehearsals.find(r => parseInt(r.Rehearsal) === rehearsalNum);
    if (rehearsalData) {
      startTime = rehearsalData['Start Time'] || '';
      endTime = rehearsalData['End Time'] || '';
      eventType = rehearsalData['Event Type'] || 'Rehearsal';
      includeInAllocation = rehearsalData['Include in allocation'] || 'Yes - Include';
      venue = rehearsalData['Venue'] || '';
      uniform = rehearsalData['Uniform'] || '';
      time = rehearsalData['Time'] || '';
    }
  }
  
  // Create modal HTML
  const modal = document.createElement('div');
  modal.style.cssText = 'position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,0.5); display:flex; align-items:center; justify-content:center; z-index:9999;';
  modal.id = 'rehearsalEditModal';
  
  const content = document.createElement('div');
  content.style.cssText = 'background:white; border-radius:8px; padding:24px; max-width:450px; width:90%; max-height:90vh; overflow-y:auto; box-shadow:0 10px 40px rgba(0,0,0,0.3);';
  
  content.innerHTML = `
    <h3 style="margin-top:0;">Edit Event ${rehearsalNum}</h3>
    
    <div style="margin-bottom:16px;">
      <label style="font-weight:bold; display:block; margin-bottom:6px;">Date</label>
      <input type="date" id="rehearsalDate" value="${date}" style="width:100%; padding:8px; border:1px solid #d1d5db; border-radius:4px; box-sizing:border-box;">
      <small style="color:#666; display:block; margin-top:4px;">Date of the event</small>
    </div>
    
    <div style="margin-bottom:16px;">
      <label style="font-weight:bold; display:block; margin-bottom:6px;">Event Type</label>
      <select id="rehearsalEventType" onchange="updateEditModalFields()" style="width:100%; padding:8px; border:1px solid #d1d5db; border-radius:4px; box-sizing:border-box;">
        <option value="Rehearsal" ${eventType === 'Rehearsal' ? 'selected' : ''}>Rehearsal</option>
        <option value="Sectional" ${eventType === 'Sectional' ? 'selected' : ''}>Sectional</option>
        <option value="Concert" ${eventType === 'Concert' ? 'selected' : ''}>Concert</option>
      </select>
      <small style="color:#666; display:block; margin-top:4px;">Type of event (Rehearsal, Sectional, or Concert)</small>
    </div>
    
    <div style="margin-bottom:16px;">
      <label style="font-weight:bold; display:block; margin-bottom:6px;">Include in allocation</label>
      <select id="rehearsalIncludeInAllocation" style="width:100%; padding:8px; border:1px solid #d1d5db; border-radius:4px; box-sizing:border-box;">
        <option value="Yes - Include" ${includeInAllocation === 'Yes - Include' ? 'selected' : ''}>Yes - Include</option>
        <option value="No - Exclude (sectional/concert)" ${includeInAllocation === 'No - Exclude (sectional/concert)' ? 'selected' : ''}>No - Exclude (sectional/concert)</option>
      </select>
      <small style="color:#666; display:block; margin-top:4px;">Whether to include in work allocation timing</small>
    </div>
    
    <div style="margin-bottom:16px;">
      <label style="font-weight:bold; display:block; margin-bottom:6px;">Start Time (HH:MM)</label>
      <input type="text" id="rehearsalStartTime" value="${startTime}" placeholder="e.g., 14:00 or 2:00 PM" style="width:100%; padding:8px; border:1px solid #d1d5db; border-radius:4px; box-sizing:border-box;">
      <small style="color:#666; display:block; margin-top:4px;">Event start time</small>
    </div>
    
    <div style="margin-bottom:16px;">
      <label style="font-weight:bold; display:block; margin-bottom:6px;">End Time (HH:MM)</label>
      <input type="text" id="rehearsalEndTime" value="${endTime}" placeholder="e.g., 17:00 or 5:00 PM" style="width:100%; padding:8px; border:1px solid #d1d5db; border-radius:4px; box-sizing:border-box;">
      <small style="color:#666; display:block; margin-top:4px;">Event end time</small>
    </div>
    
    <div style="margin-bottom:16px;" id="sectionalWorkField">
      <label style="font-weight:bold; display:block; margin-bottom:6px;">Work</label>
      <input type="text" id="rehearsalWork" value="${work}" placeholder="e.g., Elgar Variations" style="width:100%; padding:8px; border:1px solid #d1d5db; border-radius:4px; box-sizing:border-box;">
      <small style="color:#666; display:block; margin-top:4px;">Work being rehearsed (for sectionals)</small>
    </div>
    
    <div style="margin-bottom:16px;">
      <label style="font-weight:bold; display:block; margin-bottom:6px;">Section</label>
      <input type="text" id="rehearsalSection" value="${section}" placeholder="e.g., Full Ensemble, Violin 1" style="width:100%; padding:8px; border:1px solid #d1d5db; border-radius:4px; box-sizing:border-box;">
      <small style="color:#666; display:block; margin-top:4px;">'Full Ensemble' for full ensemble, or section name (e.g., Violin 1, Brass)</small>
    </div>
    
    <div style="margin-bottom:16px;">
      <label style="font-weight:bold; display:block; margin-bottom:6px;">Time</label>
      <input type="text" id="rehearsalTime" value="${time}" placeholder="e.g., 7:30 PM" style="width:100%; padding:8px; border:1px solid #d1d5db; border-radius:4px; box-sizing:border-box;">
      <small style="color:#666; display:block; margin-top:4px;">Concert time (for display)</small>
    </div>
    
    <div style="margin-bottom:16px;">
      <label style="font-weight:bold; display:block; margin-bottom:6px;">Venue</label>
      <input type="text" id="rehearsalVenue" value="${venue}" placeholder="e.g., Royal Albert Hall" style="width:100%; padding:8px; border:1px solid #d1d5db; border-radius:4px; box-sizing:border-box;">
      <small style="color:#666; display:block; margin-top:4px;">Concert venue</small>
    </div>
    
    <div style="margin-bottom:16px;">
      <label style="font-weight:bold; display:block; margin-bottom:6px;">Uniform</label>
      <input type="text" id="rehearsalUniform" value="${uniform}" placeholder="e.g., Black tie" style="width:100%; padding:8px; border:1px solid #d1d5db; border-radius:4px; box-sizing:border-box;">
      <small style="color:#666; display:block; margin-top:4px;">Dress code for concert</small>
    </div>
    
    <div style="display:flex; gap:8px; justify-content:flex-end;">
      <button onclick="document.getElementById('rehearsalEditModal').remove()" class="btn secondary" style="padding:8px 16px;">Cancel</button>
      <button onclick="saveRehearsalEdit(${rehearsalNum})" class="btn" style="padding:8px 16px;">Save Changes</button>
    </div>
  `;
  
  modal.appendChild(content);
  document.body.appendChild(modal);
  
  // Apply initial field visibility based on event type
  setTimeout(() => {
    updateEditModalFields();
    document.getElementById('rehearsalDate').focus();
  }, 100);
}

/**
 * Save event edits
 */
async function saveRehearsalEdit(rehearsalNum) {
  const dateInput = document.getElementById('rehearsalDate');
  const startTimeInput = document.getElementById('rehearsalStartTime');
  const endTimeInput = document.getElementById('rehearsalEndTime');
  const sectionInput = document.getElementById('rehearsalSection');
  const workInput = document.getElementById('rehearsalWork');
  const eventTypeSelect = document.getElementById('rehearsalEventType');
  const includeInAllocationSelect = document.getElementById('rehearsalIncludeInAllocation');
  const venueInput = document.getElementById('rehearsalVenue');
  const uniformInput = document.getElementById('rehearsalUniform');
  const timeInput = document.getElementById('rehearsalTime');
  
  if (!dateInput) return;
  
  const newDate = dateInput.value;
  const newStartTime = startTimeInput?.value?.trim() || '';
  const newEndTime = endTimeInput?.value?.trim() || '';
  const newSection = sectionInput?.value?.trim() || 'Full Ensemble';
  const newWork = workInput?.value?.trim() || '';
  const newEventType = eventTypeSelect?.value || 'Rehearsal';
  const newIncludeInAllocation = includeInAllocationSelect?.value || 'Yes - Include';
  const newVenue = venueInput?.value?.trim() || '';
  const newUniform = uniformInput?.value?.trim() || '';
  const newTime = timeInput?.value?.trim() || '';
  
  try {
    // Call API to update rehearsal details in the rehearsals table
    const res = await fetch(`${apiBase()}/s/${STATE.schedule_id}/rehearsal/${rehearsalNum}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({
        date: newDate,
        start_time: newStartTime,
        end_time: newEndTime,
        section: newSection,
        work: newWork,
        event_type: newEventType,
        include_in_allocation: newIncludeInAllocation,
        venue: newVenue,
        uniform: newUniform,
        time: newTime
      })
    });
    
    if (!res.ok) {
      console.error("Error updating event:", await res.text());
      alert("Failed to save event changes");
      return;
    }
    
    const result = await res.json();
    console.log("Event updated:", result);
    
    // Close modal
    const modal = document.getElementById('rehearsalEditModal');
    if (modal) modal.remove();
    
    // Reload the data from the server
    await reloadScheduleData();
    
    // Re-render timeline
    renderTimelineEditor();
    
  } catch (e) {
    console.error("Error saving event:", e);
    alert("Failed to save event: " + e.message);
  }
}

/**
 * Reload schedule data from server
 */
async function reloadScheduleData() {
  try {
    const res = await fetch(`${apiBase()}/s/${STATE.schedule_id}/data`, {
      method: "GET",
      credentials: "same-origin"
    });
    
    if (!res.ok) {
      console.error("Failed to reload schedule data");
      return;
    }
    
    const data = await res.json();
    if (data.timed) STATE.timed = data.timed;
    if (data.rehearsals) STATE.rehearsals = data.rehearsals;
    if (data.schedule) STATE.schedule = data.schedule;
    if (data.allocation) STATE.allocation_original = data.allocation;
    
    console.log("Schedule data reloaded");
  } catch (e) {
    console.error("Error reloading schedule data:", e);
  }
}

/**
 * Delete a rehearsal
 */
async function deleteRehearsal(rehearsalNum) {
  try {
    const scheduleId = document.body.dataset.scheduleId;
    
    // First, fetch associated concerts
    const concertsResponse = await fetch(`/api/s/${scheduleId}/rehearsal/${rehearsalNum}/concerts`);
    if (!concertsResponse.ok) {
      throw new Error('Failed to fetch associated concerts');
    }
    
    const concertsData = await concertsResponse.json();
    const associatedConcerts = concertsData.concerts || [];
    
    // If there are associated concerts, show selection modal
    if (associatedConcerts.length > 0) {
      return new Promise((resolve) => {
        showConcertSelectionModal(rehearsalNum, associatedConcerts, async (selectedConcertIds) => {
          await performRehearsalDeletion(scheduleId, rehearsalNum, selectedConcertIds);
          resolve();
        });
      });
    } else {
      // No concerts, just confirm and delete
      if (!confirm(`Are you sure you want to delete Rehearsal ${rehearsalNum}? This will permanently remove the rehearsal and all its works.`)) {
        return;
      }
      await performRehearsalDeletion(scheduleId, rehearsalNum, []);
    }
  } catch (error) {
    console.error('Error:', error);
    alert('Failed to delete rehearsal: ' + error.message);
  }
}

/**
 * Show modal to select which concerts to delete
 */
function showConcertSelectionModal(rehearsalNum, concerts, onConfirm) {
  const modal = document.createElement('div');
  modal.style.cssText = 'position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,0.5); display:flex; align-items:center; justify-content:center; z-index:10000;';
  modal.id = 'concertSelectionModal';
  
  const content = document.createElement('div');
  content.style.cssText = 'background:white; border-radius:8px; padding:24px; max-width:500px; width:90%; max-height:80vh; overflow-y:auto; box-shadow:0 10px 40px rgba(0,0,0,0.3);';
  
  const concertsCheckboxes = concerts.map((concert, idx) => `
    <div style="padding:10px; border:1px solid #e5e7eb; border-radius:4px; margin-bottom:8px; background:#f9fafb;">
      <label style="display:flex; align-items:start; cursor:pointer;">
        <input type="checkbox" id="concert-${concert.id}" value="${concert.id}" checked style="margin-right:10px; margin-top:4px;">
        <div>
          <div style="font-weight:bold;">${concert.title || 'Untitled Concert'}</div>
          <div style="font-size:0.85rem; color:#666; margin-top:2px;">${concert.date || 'No date'}</div>
        </div>
      </label>
    </div>
  `).join('');
  
  content.innerHTML = `
    <h3 style="margin-top:0; color:#dc3545;">⚠️ Delete Rehearsal ${rehearsalNum}</h3>
    <p style="margin-bottom:16px;">This rehearsal has ${concerts.length} associated concert${concerts.length > 1 ? 's' : ''}. Would you like to delete ${concerts.length > 1 ? 'them' : 'it'} as well?</p>
    <div style="margin-bottom:20px;">
      ${concertsCheckboxes}
    </div>
    <div style="display:flex; gap:8px; justify-content:flex-end; border-top:1px solid #e5e7eb; padding-top:16px;">
      <button onclick="document.getElementById('concertSelectionModal').remove()" class="btn secondary" style="padding:8px 16px;">Cancel</button>
      <button id="confirmDeleteBtn" class="btn" style="padding:8px 16px; background:#dc3545;">Delete Selected</button>
    </div>
  `;
  
  modal.appendChild(content);
  document.body.appendChild(modal);
  
  // Handle confirm button
  document.getElementById('confirmDeleteBtn').onclick = () => {
    const selectedConcertIds = concerts
      .map(c => c.id)
      .filter(id => document.getElementById(`concert-${id}`)?.checked);
    
    modal.remove();
    onConfirm(selectedConcertIds);
  };
}

/**
 * Perform the actual rehearsal deletion
 */
async function performRehearsalDeletion(scheduleId, rehearsalNum, concertIdsToDelete) {
  try {
    const response = await fetch(`/api/s/${scheduleId}/rehearsal/${rehearsalNum}`, {
      method: 'DELETE',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ concert_ids: concertIdsToDelete })
    });
    
    if (response.ok) {
      // Remove from STATE
      STATE.rehearsals = STATE.rehearsals.filter(r => parseInt(r.Rehearsal) !== rehearsalNum);
      STATE.timed = STATE.timed.filter(t => parseInt(t.Rehearsal) !== rehearsalNum);
      STATE.schedule = STATE.schedule.filter(s => parseInt(s.Rehearsal) !== rehearsalNum);
      
      // Re-render timeline
      renderTimelineEditor();
      alert(`Rehearsal ${rehearsalNum} and ${concertIdsToDelete.length} concert(s) deleted successfully`);
    } else {
      const data = await response.json();
      alert('Error deleting rehearsal: ' + (data.error || 'Unknown error'));
    }
  } catch (error) {
    console.error('Error:', error);
    alert('Failed to delete rehearsal: ' + error.message);
  }
}
window.deleteRehearsal = deleteRehearsal;

// Make functions available globally
window.renderTimelineEditor = renderTimelineEditor;
window.timelineUndo = timelineUndo;
window.timelineRedo = timelineRedo;
window.revertToOriginal = revertToOriginal;
window.openRehearsalEditModal = openRehearsalEditModal;
window.saveRehearsalEdit = saveRehearsalEdit;
window.updateEditModalFields = updateEditModalFields;
window.getRehearsalBounds = getRehearsalBounds;
