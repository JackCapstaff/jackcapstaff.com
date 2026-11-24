# jackcapstaff.com
Website

## Click tracks with video

In the signed-in Click Track page, open **Track actions → New click track from
video**. The same dropdown has the new track and JSON options, and **Pedal
setup** when you're in Edit.
Paste a YouTube video link and select **Load YouTube video**.
Use **Play video** or **Enter** to play or pause without changing hit points.
Press **Space** or select **Add hit point** to mark the current time and pause.
Type the **Score bar** and press **Enter** to keep it and resume playback.
When score bars are omitted from the film, select the hit point at the start
of the span and enter their count in **Score bars skipped before the next
hit**. For example, with hit points at bars 129 and 131, set bar 129 to 9/8
and enter 1 skipped bar to combine score bars 129 and 130 into one played
9/8 bar. The display then jumps directly to bar 131. Skipped bars take no time.
If the embedded player has keyboard focus, use the controls below it.

Enable **Preview click with video** in the video controls to hear the current
draft as the video plays, without switching to Perform. It follows seeking,
pauses, playback speed, tempo changes and timed pauses, and updates when hit
points change. Linked tracks use matching section click and sound settings;
otherwise new-section defaults apply. Invalid hit-point plans are reported
and cannot be previewed until corrected. The click uses the selected click
audio output and volume. YouTube playback and seeking are approximate.
The preview turns off when leaving the editor.
The video editor also shows the current section, bar, meter, tempo and beat
visuals at the video position, even when click preview is off. It uses the
performance beat-visual preference and shows the next tempo during a timed
pause; seeking or changing the draft updates the display.
During a timed pause and its count-in, a fermata appears beside the pendulum
in both displays. The performance display also shows it during a pedal-held
cue and its count-in. It disappears when normal playback begins.

To revise a draft, select a hit point and play from there. **Pause just before each
hit point** stops playback at the next marker so you can edit, move or remove
it. Untick **Pause just before each hit point** to play through them. With
click preview enabled, the next hit point's click is held back until playback
resumes. YouTube seeking and pause timing are approximate. Add new hit points
between stops as usual. Inserting a point between
16 and 17 suggests **16A** without renumbering either neighbour. To shift the
labels of later points, tick **Renumber following hit points** before editing
**Hit point number**. Changing **13** to **12A** then shifts **14** to **13**,
**15** to **14**, and so on, keeping any letter suffixes on the later points.
With the option off, only the selected point changes. Neither mode changes
score bars or timestamps. Labels are kept when you save a YouTube draft.
The click-track preview uses the same hit-point numbers as the list, including
letter suffixes; each row shows the starting and closing hit points.
To replace a marker's time
without changing its details, select it, seek to the new time on the timeline
and choose **Move hit point to playhead**. Use the **−0.1 s** and **+0.1 s**
buttons in the hit-point details to make smaller adjustments.

During playback the hit-point list follows and scrolls to the current point,
updating the selected section controls. Following waits until you finish editing
an active hit-point or section field so your input is not interrupted. If
pause-before-hit is enabled, stopping commits the active field before selecting
the upcoming hit point.

Hit-point changes save automatically when signed in. An incomplete new
video draft can be reopened under **Import an existing video draft**; once it
has a valid timing plan, a linked click track is created automatically and
appears in the track list. Open that track and select **Edit** to see its full
track editor alongside the video and hit points. Collapse **Video and hit
points** when you want to focus on sections. The video itself stays on YouTube,
requires internet access and must allow embedding.
Use **Export hit points (.csv)** in the video editor to download all current
hit-point fields, including unsaved edits. Times are in seconds; fields beginning
with spreadsheet formula characters are prefixed with an apostrophe for safety.
Selecting an opening hit point also shows its matching track section directly
within its hit-point controls. Shared settings appear once, alongside the
additional track settings; timing derived from the video remains editable
through the hit-point controls. **Jump to full track section** scrolls to the
matching section, and **Back to video** returns to the video area.
Closing points and points without a unique saved section do not have section
settings until a valid plan creates or matches them.
In the full track editor, choose a section under **Jump to section** and press
the button to scroll to its fields. The list includes hit points, figures and
score bars when available; this does not change the playback position.

For a portable backup, select the track and choose **Track actions → Export
track backup (.json)**. If a saved YouTube draft is linked, the file includes
both the track and the last saved draft: the video ID and all hit-point data,
including times, bars, labels, meter, beat unit, tempo shape and ramp anchor, dialogue, figures,
skips and visual cues. Wait for changes to finish saving first; export fails rather
than silently omitting a linked draft if it cannot be loaded. A track without
a linked draft exports as a track-only backup. The video itself is not included.
To restore a combined backup, sign in online and choose **Load track (.json)**.
This creates a new track and a new linked draft without changing the originals.
Older track-only JSON files can still be imported.

Linked track and hit-point edits save together automatically. Meter, beat unit,
cue text, figure, hit-point label and visual cues synchronize in both directions;
hit-point times determine tempo. Edit timing, score bars and tempo shape using the video
hit points. Sections are matched by hit-point time or label before score bar, so
changing a score bar retains the section's settings. Matching sections retain their names, click and sound settings,
grouping, advance and count-in; the track title and track-level notes stay.
If sections have no unique match, a confirmation lists the sections and their
settings before removal. Declining preserves the playable track. Incomplete or
invalid hit-point edits are saved separately while the last valid track remains
playable; correct them to update it. A save error is shown, and linked unsaved
changes are kept in the browser for retry.

For older tracks, open the matching saved draft and choose **Link open draft to
track** once. When opening a linked draft, differences in meter, beat unit,
cues and tempo shape are displayed so you can choose the draft or track values.
If another track shares the draft, editing creates a separate copy. Concurrent
changes are rejected rather than silently overwritten.
For accelerandos and rallentandos, choose a **Ramp tempo anchor**: fix the
starting or ending BPM, or match the previous section's end or the next
section's start. The other endpoint is calculated from the hit times and beat
count, converting beat units where necessary. If the result requires the
opposite direction or a steady tempo, confirm before changing the shape.
Declining preserves the last playable track while retaining the draft edits.
When matching the next section, optionally enable **Metric modulation** and
choose the old and new note values: the old note at the ramp end equals the
new note at the following section's start. For example, old quaver = new
crotchet with the next crotchet at 86 gives a ramp endpoint of crotchet 43.
Straight, dotted, triplet (3:2), quintuplet (5:4) and septuplet (7:4) values
are available from semibreves through semiquavers. The next section's tempo
is still determined by its video timing; the modulation sets the ramp endpoint.
Matching needs an independent neighbouring click-section tempo; circular
dependencies and out-of-range tempos are reported. Tempo changes linearly
over time, and clicks follow the resulting curve.
Saved older ramps keep their original timing unless regenerated from video.
For a fixed pause, select its opening hit point and choose **Timed pause and
count-in** under **Tempo shape**. The span stays silent until a count-in at the
next section's starting tempo, ending at the next hit point. It uses up to one
bar of whole tempo beats; if a full bar does not fit, it uses as many whole
beats as fit. The next span must be a click section, and at least one beat must
fit. Choose **Count-in subdivisions per beat** on the timed-pause section
to add two, three or four clicks per beat; the silent portion stays silent.
Subdivision pitch and volume come from the following section's subdivision
sound settings. A ramp immediately after a timed pause cannot match the pause's
tempo; use a fixed endpoint or match the next section instead.
Tempo beats use the next section's beat unit (including dotted units for
compound meters), not irregular grouping. Change the video hit points to adjust
the pause's duration and regenerate synchronised timings. Starting at a timed
pause plays its silence and built-in count-in;
the separate pedal-controlled pause still waits indefinitely for a cue.
WAV exports split at timed pauses and do not include them.
Select a musical hit point and choose **Perform from here** to play the saved
linked track from that point after changes finish saving. The selected score
bar must uniquely match a playable section.
To resync a draft, enter a signed shift in seconds under **Resync hit-point
times**, preview it, then apply it. Musical points shift together and the
0:00 reference stays fixed. Applying the shift saves it automatically. To switch to
another YouTube link without losing the hit points, enter it and choose
**Replace video, keep hit points**.

Set **Starting count-in (bars)** in Edit, or when creating a track from video.
After creation, edit the track's count-in in the full track editor.
For a new section or track, a blank count-in automatically uses the fewest
whole bars lasting at least two seconds at that section's starting tempo and
meter. Change the field to a number to set an explicit count-in, including 0
for none; clear it to restore automatic timing. Existing saved numeric
count-ins are unchanged.
For a one-off rehearsal start, open **Perform → Rehearsal points**, pause if
playing, then choose a section or enter any playable score bar. Choose a
**Rehearsal count-in** and select **Start from here**. A skipped score bar
cannot be selected, and an ambiguous repeated bar requires choosing a section.
If the section's saved count-in is 0, rehearsal restarts default to enough whole
bars for at least two seconds at the restart tempo. Other saved count-in
settings are respected. Choose **No count-in** to start without a lead-in for
that restart; this does not change the saved setting or regular playback.
Under **Performance options → Beat visual**, choose the existing circles,
a swinging metronome, or both. The swing changes direction on each grouped
beat, follows tempo ramps and freezes when playback is paused. Your choice
is saved on this browser and included when preparing an offline show.
In fullscreen, open **Rehearsal points** on the right. Pause playback and
select a section to see its figure and score bar, choose a one-off count-in
in the dialog, then select **Start from here**. Cancel leaves playback paused.
Silent cues still wait for the pedal. The list also works in the offline view.
The current section is highlighted, and the list follows section changes and
returns to the current section when reopened. You can scroll manually between
changes. Use **Edit** beside a rehearsal point to open its quick editor;
editing stops playback. Editing is unavailable in the read-only offline copy.
The fixed column headings show hit point, figure and score bar. Video-generated
sections retain their hit-point labels, including letter suffixes; existing
linked tracks can display labels from uniquely matching saved video hit points.
Sections without a label show a dash, and labels can also be edited in the
section editor. Prepare the offline show again to include these labels.
While playback is running, selecting a rehearsal point queues a jump at the
next barline (or jumps immediately from a silent cue). Pause first to choose a
count-in and restart instead.

Use **Edit current section** in Perform, including fullscreen, to open the
same section fields without leaving the stage. Opening it stops playback;
changes save automatically. Choose **Perform from here** to close the editor
and start that section. The offline performance copy is read-only and has no quick editor.
Press **Escape** to close the quick editor without leaving fullscreen.
Choose **Edit with video** in the quick editor to open the linked draft at the
matching score-bar hit point. If there is no unique matching hit point, the
video opens at the track's estimated section time instead.
Perform displays rounded BPM without changing the saved tempo or click timing.
The next-section preview compares the rounded conducting beats per minute
directly, even when the beat unit changes. For example, crotchet = 90 to
dotted crotchet = 60 shows **Slower (-30 BPM)**, with a beat-unit change.

Enter an optional **Rehearsal figure** (for example `6` or `A`) on a section in
Edit, or on the opening video hit point for that section. Figures appear in the
**Rehearsal points** list alongside their score bars. Between figures the list
shows the nearest figure in bold with a signed score-bar offset: `7 − 3`
means three bars before figure 7, and `6 + 2` means two bars after figure 6.
Equal distances use the preceding figure. Skipped score bars are counted by
their printed bar numbers; cue-only sections without score bars have no offset.

Compound meters such as 6/8 and 9/8 default to **Every meter unit**, so all
quavers sound. Existing compound sections previously set to **Beats only**
are converted once; you can choose **Beats only** again and that choice is
preserved. Other click settings are unchanged.

For rehearsal, video is enabled by default in **Perform** when a video is
linked. The film and metronome
sit side by side on wider screens. On narrow screens the beat display,
bar, meter and tempo overlay the bottom of the picture, with a cue strip
at the top. Untick the box to close the player without stopping the clicks.
If no video is linked, use **Edit with video** in that panel to open and link
a saved YouTube draft.
The film follows click playback and holds during cue waits. Practice opens
the YouTube link from the draft linked to the track. For a count-in, the film
starts that many bars before the selected section’s video position, using the
section’s starting tempo and meter. If there is not enough footage, the
count-in still plays in full and the picture waits at 0:00 until it can roll.
Choose **Start video from beginning** to rehearse from 0:00 instead of
jumping to the saved count-in. This mode counts every beat that fits
before the first musical cue, using the opening tempo and meter and
counting back from the downbeat. For example, 60 seconds at 60 BPM in 4/4
shows −60, −59, …, −1, then bar 1. If the film begins between beats, it
rolls silently until the first beat that fits.
Film audio is muted by default. YouTube sync is approximate; use **Resync to
clicks** after buffering. Video can also be enabled in fullscreen; the offline
stage view remains video-free.

Tick **Streamer and punch** on the video hit points that need a visual cue.
You can also mark section boundaries in
Edit. A marked opening hit can also get a streamer when rehearsing from
0:00 if there are at least two seconds before the hit. For later hits, the
streamer starts on the nearest preceding
barline that gives at least two seconds of warning, using the scheduled tempo,
meter and ramps. The punch lands on the click cue. If there is not enough
continuous playback before a cue, only the punch appears. On wider screens, cues overlay a local
video and appear in a strip above YouTube, whose sync remains approximate.
