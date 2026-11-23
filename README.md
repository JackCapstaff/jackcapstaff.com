# jackcapstaff.com
Website

## Click track video drafts

In the signed-in Click Track page, open **Track actions → New click track from
video**. The same dropdown has the new track and JSON options, and **Pedal
setup** when you're in Edit.
Choose a local MP4, or paste a YouTube video link and select **Load YouTube video**.
Use **Play video** or **Enter** to play or pause without changing hit points.
Press **Space** or select **Add hit point** to mark the current time and pause.
Type the **Score bar** and press **Enter** to keep it and resume playback.
When score bars are omitted from the film, enter their count in **Score bars
skipped before this hit** on the next hit point. For example, with hit points
at bars 10 and 12, enter 1 on bar 12 to fit one played bar between them; the
display then jumps directly from bar 10 to bar 12. Skipped bars take no time.
If the embedded player has keyboard focus, use the controls below it.

To revise a draft, select a hit point and play from there. **Pause at existing
hit points** stops playback at the next marker so you can edit, move or remove
it. Add new hit points between stops as usual. Inserting a point between
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
and choose **Move hit point to playhead**.

Select **Save YouTube draft** to store the video reference and hit points in
your account, then use **Saved YouTube drafts** to reopen them on another
device. Save again after making changes. The video itself stays on YouTube,
requires internet access and must allow embedding. Local files are not uploaded
and their drafts stay in the current tab.

**Create click track** makes a separate track in the normal editor; the finished
track does not need the video. For YouTube, this also saves and links the draft.
Open the track's **Edit** tab and choose **Edit with video** to reopen it. For
older tracks, open the matching saved draft and choose **Link open draft to
track** once. Editing or saving the draft does not overwrite the finished
track. To apply revised hit points, pick an **Existing click track** and
choose **Save over existing track**. This replaces its sections and video
offset, keeping its title and track-level notes. Local
video remains available to revisit only while it is still open in the tab.
For tighter timing, use a local file rather than relying on YouTube seeking.
To resync a draft, enter a signed shift in seconds under **Resync hit-point
times**, preview it, then apply it. Musical points shift together and the
0:00 reference stays fixed. Save the draft, then use **Save over existing
track** if the finished track should use the shifted timings. To switch to
another YouTube link without losing the hit points, enter it and choose
**Replace video, keep hit points**; saving updates the draft used by linked
tracks. Alternatively, adjust **Video offset** in the regular track editor to
shift the finished track without changing the draft.

Set **Starting count-in (bars)** in Edit, or when creating a track from video.
When saving over a track, leaving the field blank keeps its current count-in.
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

Enter an optional **Rehearsal figure** (for example `6` or `A`) on a section in
Edit, or on the opening video hit point for that section. Figures appear in the
**Rehearsal points** list alongside their score bars.

For rehearsal, tick **Enable video** in **Perform**. The film and metronome
sit side by side on wider screens. On narrow screens the beat display,
bar, meter and tempo overlay the bottom of the picture, with a cue strip
at the top. Untick the box to close the player without stopping the clicks.
If no video is linked, use **Edit with video** in that panel to open and link
a saved YouTube draft, or use a local file still open in the tab.
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

Tick **Streamer and punch** on the video hit points that need a visual cue,
then create or update the click track. You can also mark section boundaries in
Edit. A marked opening hit can also get a streamer when rehearsing from
0:00 if there are at least two seconds before the hit. For later hits, the
streamer starts on the nearest preceding
barline that gives at least two seconds of warning, using the scheduled tempo,
meter and ramps. The punch lands on the click cue. If there is not enough
continuous playback before a cue, only the punch appears. On wider screens, cues overlay a local
video and appear in a strip above YouTube, whose sync remains approximate.
