// conductor.js - Live rehearsal conducting mode

const STATE = {
  scheduleId: window.SCHEDULE_ID,
  rehearsalNum: window.REHEARSAL_NUM,
  rehearsal: null,
  timedItems: [],
  currentIndex: 0,
  isPaused: false,
  startTime: null,
  timeOffset: 0, // minutes
  currentWorkStartedAt: null,
  warningTimes: [5, 2, 1], // minutes before end
  warningsShown: new Set(),
  log: {
    rehearsal_num: window.REHEARSAL_NUM,
    started_at: null,
    ended_at: null,
    items: [],
    adjustments: []
  },
  autoSaveInterval: null
};

// ========================
// Initialization
// ========================

async function init() {
  console.log('[CONDUCTOR] Initializing...');
  
  // Load data from API
  try {
    const response = await fetch(`/api/s/${STATE.scheduleId}/conduct/${STATE.rehearsalNum}/data`);
    const data = await response.json();
    
    if (!data.ok) {
      alert('Failed to load rehearsal data: ' + (data.error || 'Unknown error'));
      return;
    }
    
    STATE.rehearsal = data.rehearsal;
    STATE.timedItems = data.timed_items || [];
    
    console.log('[CONDUCTOR] Loaded:', {
      rehearsal: STATE.rehearsal,
      items: STATE.timedItems.length
    });
    
    // Populate start work selector
    const select = document.getElementById('startWorkSelect');
    select.innerHTML = '<option value="0">First work</option>';
    STATE.timedItems.forEach((item, idx) => {
      const option = document.createElement('option');
      option.value = idx;
      option.textContent = `${idx + 1}. ${item.Title} (${item['Time in Rehearsal']})`;
      select.appendChild(option);
    });
    
    // Show scheduled start time
    const scheduledStart = STATE.rehearsal['Start Time'] || '19:00:00';
    document.getElementById('scheduledStart').textContent = formatTime(scheduledStart);
    
    // Check if we should auto-start
    checkAutoStart();
    
  } catch (error) {
    console.error('[CONDUCTOR] Error loading data:', error);
    alert('Failed to load rehearsal data. Please try again.');
  }
  
  // Setup event listeners
  setupEventListeners();
  
  // Start clock
  updateClock();
  setInterval(updateClock, 1000);
}

function setupEventListeners() {
  document.getElementById('startBtn').addEventListener('click', startConducting);
  document.getElementById('exitBtn').addEventListener('click', exitConducting);
  document.getElementById('nextBtn').addEventListener('click', nextWork);
  document.getElementById('prevBtn').addEventListener('click', prevWork);
  document.getElementById('pauseBtn').addEventListener('click', togglePause);
  document.getElementById('reorderBtn').addEventListener('click', showReorderModal);
  document.getElementById('settingsBtn').addEventListener('click', showSettingsModal);
}

function checkAutoStart() {
  if (!STATE.rehearsal) return;
  
  const now = new Date();
  const scheduledStart = STATE.rehearsal['Start Time'] || '19:00:00';
  const scheduledDateStr = STATE.rehearsal['Date'] || new Date().toISOString().split('T')[0];
  
  // Parse scheduled start - handle both date-only and datetime formats
  let scheduledDate = scheduledDateStr;
  if (scheduledDateStr.includes('T') || scheduledDateStr.includes(' ')) {
    // Extract just the date part if it's a datetime
    scheduledDate = scheduledDateStr.split('T')[0].split(' ')[0];
  }
  
  // Parse scheduled start time
  const [hours, minutes] = scheduledStart.split(':').map(Number);
  const scheduledDateTime = new Date(scheduledDate + 'T00:00:00');
  scheduledDateTime.setHours(hours, minutes, 0, 0);
  
  // Check if we're within 1 hour before start time and it's today
  const diffMs = scheduledDateTime - now;
  const diffMins = diffMs / (1000 * 60);
  
  // Compare dates (normalize to just date strings)
  const nowDateStr = now.toISOString().split('T')[0];
  const scheduledDateOnly = scheduledDateTime.toISOString().split('T')[0];
  const isToday = nowDateStr === scheduledDateOnly;
  const isWithinHour = diffMins <= 60 && diffMins >= -10;
  
  console.log('[CONDUCTOR] Date check:', {
    now: nowDateStr,
    scheduled: scheduledDateOnly,
    isToday,
    diffMins: Math.round(diffMins)
  });
  
  if (isToday && isWithinHour) {
    console.log('[CONDUCTOR] Within auto-start window');
    if (diffMins <= 0 && diffMins >= -10) {
      showAlert('Rehearsal has started! Click "Start Rehearsal" when ready.', 'success');
    } else if (diffMins > 0) {
      const minsUntil = Math.ceil(diffMins);
      showAlert(`Rehearsal starts in ${minsUntil} minute${minsUntil !== 1 ? 's' : ''}.`, 'info');
    }
  } else if (!isToday && diffMins > 60) {
    const daysAway = Math.ceil(diffMs / (1000 * 60 * 60 * 24));
    showAlert(`This rehearsal is scheduled for ${scheduledDateOnly} (${daysAway} day${daysAway !== 1 ? 's' : ''} away). You can still start it manually.`, 'info');
  } else if (!isToday && diffMins < 0) {
    showAlert(`This rehearsal was scheduled for ${scheduledDateOnly}. You can still start it manually.`, 'info');
  }
}

// ========================
// Clock & Time Utils
// ========================

function updateClock() {
  const now = new Date();
  const timeStr = now.toLocaleTimeString('en-GB', { 
    hour: '2-digit', 
    minute: '2-digit', 
    second: '2-digit' 
  });
  document.getElementById('liveClock').textContent = timeStr;
  
  // Update conducting state if active
  if (STATE.startTime && !STATE.isPaused) {
    updateCountdown();
  }
}

function formatTime(timeStr) {
  if (!timeStr) return '--:--';
  const parts = timeStr.split(':');
  return `${parts[0]}:${parts[1]}`;
}

function timeToMinutes(timeStr) {
  if (!timeStr) return 0;
  const parts = timeStr.split(':').map(Number);
  return parts[0] * 60 + parts[1];
}

function minutesToTime(minutes) {
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:00`;
}

function formatDuration(minutes) {
  if (minutes < 0) return `+${Math.abs(minutes)}min over`;
  const mins = Math.floor(minutes);
  const secs = Math.floor((minutes - mins) * 60);
  return `${mins}:${String(secs).padStart(2, '0')}`;
}

// ========================
// Conducting Control
// ========================

function startConducting() {
  console.log('[CONDUCTOR] Starting...');
  
  // Get settings
  const startWorkIdx = parseInt(document.getElementById('startWorkSelect').value);
  STATE.timeOffset = parseInt(document.getElementById('timeOffset').value) || 0;
  STATE.warningTimes = [
    parseInt(document.getElementById('warning1').value) || 5,
    parseInt(document.getElementById('warning2').value) || 2,
    parseInt(document.getElementById('warning3').value) || 1
  ].sort((a, b) => b - a); // Descending order
  
  STATE.currentIndex = startWorkIdx;
  STATE.startTime = new Date();
  STATE.isPaused = false;
  STATE.log.started_at = STATE.startTime.toISOString();
  STATE.log.items = [];
  STATE.log.adjustments = [];
  
  // Hide pre-start, show conducting
  document.getElementById('preStartScreen').style.display = 'none';
  document.getElementById('conductingScreen').style.display = 'block';
  
  // Start first work
  startWork(STATE.currentIndex);
  
  // Start auto-save
  STATE.autoSaveInterval = setInterval(autoSave, 30000); // Every 30 seconds
  
  updateDisplay();
}

function startWork(index) {
  if (index < 0 || index >= STATE.timedItems.length) return;
  
  const work = STATE.timedItems[index];
  const now = new Date();
  
  STATE.currentIndex = index;
  STATE.currentWorkStartedAt = now;
  STATE.warningsShown.clear();
  
  console.log('[CONDUCTOR] Starting work:', work.Title);
  
  // Log this work
  const scheduledStart = work['Time in Rehearsal'];
  const scheduledDuration = parseInt(work['Rehearsal Time (minutes)']) || 
                           calculateDuration(index);
  
  STATE.log.items.push({
    title: work.Title,
    scheduled_start: scheduledStart,
    scheduled_duration: scheduledDuration,
    scheduled_duration_seconds: scheduledDuration * 60,
    actual_start: now.toISOString(),
    actual_duration: null, // Will be filled when moving to next
    actual_duration_seconds: null,
    status: 'in_progress'
  });
  
  updateDisplay();
}

function calculateDuration(index) {
  // Calculate duration from gap to next work
  if (index < STATE.timedItems.length - 1) {
    const currentStart = timeToMinutes(STATE.timedItems[index]['Time in Rehearsal']);
    const nextStart = timeToMinutes(STATE.timedItems[index + 1]['Time in Rehearsal']);
    return nextStart - currentStart;
  }
  
  // Last item: use rehearsal end time
  const currentStart = timeToMinutes(STATE.timedItems[index]['Time in Rehearsal']);
  const rehearsalEnd = timeToMinutes(STATE.rehearsal['End Time']);
  return rehearsalEnd - currentStart;
}

function nextWork() {
  if (STATE.currentIndex >= STATE.timedItems.length - 1) {
    if (confirm('This is the last work. End rehearsal?')) {
      endConducting();
    }
    return;
  }
  
  // Complete current work
  completeCurrentWork();
  
  // Move to next
  startWork(STATE.currentIndex + 1);
}

function prevWork() {
  if (STATE.currentIndex <= 0) {
    showAlert('Already at first work', 'warning');
    return;
  }
  
  // Mark current as skipped
  if (STATE.log.items.length > 0) {
    STATE.log.items[STATE.log.items.length - 1].status = 'skipped';
  }
  
  STATE.log.adjustments.push({
    time: new Date().toISOString(),
    action: 'went_back',
    from_index: STATE.currentIndex,
    to_index: STATE.currentIndex - 1
  });
  
  // Move to previous
  startWork(STATE.currentIndex - 1);
}

function completeCurrentWork() {
  if (STATE.log.items.length > 0) {
    const lastLog = STATE.log.items[STATE.log.items.length - 1];
    const now = new Date();
    const started = new Date(lastLog.actual_start);
    const durationSecs = (now - started) / 1000;
    
    // Store duration in seconds for accuracy
    lastLog.actual_duration_seconds = Math.round(durationSecs);
    lastLog.actual_duration = Math.round(durationSecs / 60); // Keep minutes for backward compatibility
    lastLog.status = 'completed';
  }
}

function togglePause() {
  STATE.isPaused = !STATE.isPaused;
  
  const btn = document.getElementById('pauseBtn');
  if (STATE.isPaused) {
    btn.innerHTML = '<i class="bi bi-play-fill"></i> Resume';
    btn.classList.remove('warning');
    btn.classList.add('success');
    
    STATE.log.adjustments.push({
      time: new Date().toISOString(),
      action: 'paused'
    });
  } else {
    btn.innerHTML = '<i class="bi bi-pause-fill"></i> Pause';
    btn.classList.remove('success');
    btn.classList.add('warning');
    
    STATE.log.adjustments.push({
      time: new Date().toISOString(),
      action: 'resumed'
    });
  }
  
  updateDisplay();
}

function endConducting() {
  completeCurrentWork();
  
  STATE.log.ended_at = new Date().toISOString();
  
  // Save final log
  saveLog().then(() => {
    // Clear auto-save
    if (STATE.autoSaveInterval) {
      clearInterval(STATE.autoSaveInterval);
    }
    
    // Redirect to report
    window.location.href = `/conduct/${STATE.scheduleId}/${STATE.rehearsalNum}/report`;
  });
}

function exitConducting() {
  if (!STATE.startTime) {
    // Not started yet, just go back
    window.location.href = `/s/${STATE.scheduleId}`;
    return;
  }
  
  if (confirm('Exit conducting mode? Your progress will be saved.')) {
    STATE.log.ended_at = new Date().toISOString();
    
    saveLog().then(() => {
      if (STATE.autoSaveInterval) {
        clearInterval(STATE.autoSaveInterval);
      }
      window.location.href = `/s/${STATE.scheduleId}`;
    });
  }
}

// ========================
// Display Updates
// ========================

function updateDisplay() {
  if (STATE.currentIndex < 0 || STATE.currentIndex >= STATE.timedItems.length) return;
  
  const currentWork = STATE.timedItems[STATE.currentIndex];
  const isBreak = currentWork.Title === 'Break';
  
  // Update current work
  document.getElementById('currentTitle').textContent = currentWork.Title;
  
  const scheduledDuration = parseInt(currentWork['Rehearsal Time (minutes)']) || 
                           calculateDuration(STATE.currentIndex);
  const scheduledStart = currentWork['Time in Rehearsal'];
  
  let metaText = `${scheduledDuration} minutes`;
  if (!isBreak) {
    metaText += ` • Starts at ${formatTime(scheduledStart)}`;
  }
  document.getElementById('currentMeta').textContent = metaText;
  
  // Update next work
  if (STATE.currentIndex < STATE.timedItems.length - 1) {
    const nextWork = STATE.timedItems[STATE.currentIndex + 1];
    document.getElementById('nextTitle').textContent = nextWork.Title;
    const nextDuration = parseInt(nextWork['Rehearsal Time (minutes)']) || 
                        calculateDuration(STATE.currentIndex + 1);
    const nextStart = nextWork['Time in Rehearsal'];
    document.getElementById('nextMeta').textContent = 
      `${nextDuration} minutes • ${formatTime(nextStart)}`;
    document.getElementById('nextWork').style.display = 'block';
  } else {
    document.getElementById('nextWork').style.display = 'none';
  }
  
  // Update button states
  document.getElementById('prevBtn').disabled = STATE.currentIndex === 0;
  document.getElementById('nextBtn').textContent = 
    STATE.currentIndex === STATE.timedItems.length - 1 ? 'Finish' : 'Next';
}

function updateCountdown() {
  if (!STATE.currentWorkStartedAt || STATE.isPaused) return;
  
  const now = new Date();
  const elapsed = (now - STATE.currentWorkStartedAt) / (1000 * 60); // minutes
  const scheduledDuration = parseInt(STATE.timedItems[STATE.currentIndex]['Rehearsal Time (minutes)']) || 
                           calculateDuration(STATE.currentIndex);
  
  const remaining = scheduledDuration - elapsed;
  const progress = Math.min(100, (elapsed / scheduledDuration) * 100);
  
  // Update countdown display
  const countdownEl = document.getElementById('countdown');
  countdownEl.textContent = formatDuration(Math.max(0, remaining));
  
  // Update progress bar
  const progressFill = document.getElementById('progressFill');
  progressFill.style.width = `${progress}%`;
  
  // Update visual state
  const currentWorkEl = document.getElementById('currentWork');
  
  if (remaining < 0) {
    // Overdue!
    countdownEl.classList.remove('normal', 'warning', 'danger');
    countdownEl.classList.add('overdue');
    progressFill.classList.remove('warning');
    progressFill.classList.add('danger');
    currentWorkEl.classList.remove('normal', 'warning');
    currentWorkEl.classList.add('danger');
    
    if (!STATE.warningsShown.has('overdue')) {
      showAlert('Time exceeded! Move to next work.', 'danger');
      STATE.warningsShown.add('overdue');
    }
  } else {
    // Check warning thresholds
    let currentState = 'normal';
    
    for (const warningMins of STATE.warningTimes) {
      if (remaining <= warningMins && !STATE.warningsShown.has(warningMins)) {
        const workTitle = STATE.timedItems[STATE.currentIndex].Title;
        showAlert(`${warningMins} minute${warningMins > 1 ? 's' : ''} remaining: ${workTitle}`, 'warning');
        STATE.warningsShown.add(warningMins);
      }
      
      if (remaining <= warningMins) {
        currentState = warningMins <= 1 ? 'danger' : 'warning';
        break;
      }
    }
    
    countdownEl.classList.remove('normal', 'warning', 'danger', 'overdue');
    countdownEl.classList.add(currentState);
    
    progressFill.classList.remove('warning', 'danger');
    if (currentState !== 'normal') {
      progressFill.classList.add(currentState);
    }
    
    currentWorkEl.classList.remove('normal', 'warning', 'danger');
    currentWorkEl.classList.add(currentState);
  }
}

// ========================
// Reordering
// ========================

function showReorderModal() {
  const modal = document.getElementById('reorderModal');
  const list = document.getElementById('reorderList');
  
  list.innerHTML = '';
  
  STATE.timedItems.forEach((item, idx) => {
    if (idx <= STATE.currentIndex) return; // Can't reorder past/current
    
    const div = document.createElement('div');
    div.className = 'work-item';
    if (item.Title === 'Break') {
      div.classList.add('break');
    }
    
    div.innerHTML = `
      <div class="work-item-title">${item.Title}</div>
      <div class="work-item-meta">
        ${formatTime(item['Time in Rehearsal'])} • 
        ${parseInt(item['Rehearsal Time (minutes)']) || calculateDuration(idx)} minutes
      </div>
    `;
    
    div.addEventListener('click', () => {
      selectNextWork(idx);
      closeReorderModal();
    });
    
    list.appendChild(div);
  });
  
  modal.classList.add('active');
}

function closeReorderModal() {
  document.getElementById('reorderModal').classList.remove('active');
}

function selectNextWork(targetIndex) {
  if (targetIndex <= STATE.currentIndex) return;
  
  // Complete current work
  completeCurrentWork();
  
  // Log the reordering
  STATE.log.adjustments.push({
    time: new Date().toISOString(),
    action: 'reordered',
    from_index: STATE.currentIndex,
    to_index: targetIndex,
    skipped_works: STATE.timedItems.slice(STATE.currentIndex + 1, targetIndex).map(w => w.Title)
  });
  
  // Mark skipped works
  const skipCount = targetIndex - STATE.currentIndex - 1;
  for (let i = 0; i < skipCount; i++) {
    STATE.log.items.push({
      title: STATE.timedItems[STATE.currentIndex + 1 + i].Title,
      scheduled_start: STATE.timedItems[STATE.currentIndex + 1 + i]['Time in Rehearsal'],
      scheduled_duration: calculateDuration(STATE.currentIndex + 1 + i),
      actual_start: null,
      actual_duration: null,
      status: 'skipped'
    });
  }
  
  // Start selected work
  startWork(targetIndex);
  
  showAlert(`Skipped to: ${STATE.timedItems[targetIndex].Title}`, 'warning');
}

// ========================
// Settings
// ========================

function showSettingsModal() {
  document.getElementById('settingsWarning1').value = STATE.warningTimes[0];
  document.getElementById('settingsWarning2').value = STATE.warningTimes[1];
  document.getElementById('settingsWarning3').value = STATE.warningTimes[2];
  
  document.getElementById('settingsModal').classList.add('active');
}

function closeSettingsModal() {
  document.getElementById('settingsModal').classList.remove('active');
}

function saveSettings() {
  STATE.warningTimes = [
    parseInt(document.getElementById('settingsWarning1').value) || 5,
    parseInt(document.getElementById('settingsWarning2').value) || 2,
    parseInt(document.getElementById('settingsWarning3').value) || 1
  ].sort((a, b) => b - a);
  
  STATE.warningsShown.clear(); // Reset warnings with new times
  
  closeSettingsModal();
  showAlert('Settings saved', 'success');
}

// ========================
// Alerts
// ========================

function showAlert(message, type = 'info') {
  const alert = document.createElement('div');
  alert.className = `alert ${type}`;
  alert.textContent = message;
  
  document.body.appendChild(alert);
  
  setTimeout(() => {
    alert.remove();
  }, 5000);
}

// ========================
// Logging & Saving
// ========================

async function saveLog() {
  try {
    const response = await fetch(`/api/s/${STATE.scheduleId}/conduct/${STATE.rehearsalNum}/log`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        log: STATE.log
      })
    });
    
    const result = await response.json();
    
    if (!result.ok) {
      console.error('[CONDUCTOR] Failed to save log:', result.error);
    } else {
      console.log('[CONDUCTOR] Log saved successfully');
    }
  } catch (error) {
    console.error('[CONDUCTOR] Error saving log:', error);
  }
}

async function autoSave() {
  if (STATE.startTime && !STATE.isPaused) {
    console.log('[CONDUCTOR] Auto-saving...');
    await saveLog();
  }
}

// ========================
// Start
// ========================

document.addEventListener('DOMContentLoaded', init);

// Prevent accidental navigation away
window.addEventListener('beforeunload', (e) => {
  if (STATE.startTime && !STATE.log.ended_at) {
    e.preventDefault();
    e.returnValue = '';
  }
});
