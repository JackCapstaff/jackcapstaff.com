# jackcapstaff.com
Website

## Performance Studio home

The site's single **Performance Studio** navigation link opens `/live/home/`.
All signed-in users can open **Click Tracks** from this home screen. Only
administrators see **Media Centre**, and its pages and management APIs also
enforce admin permissions. Public audience links remain accessible without
login. Tool headers link back to the home screen.

Existing `/live/` and `/clicktrack/` links still open Click Tracks directly;
presentation editor, controller and audience URLs are unchanged.

## Media Centre

Administrators can open **Media Centre** from Performance Studio home or visit
`/presentations/`. This is a separate, browser-rendered presentation builder,
not a video-file exporter; click-track integration is deliberately deferred.
Each admin manages their own presentations and reusable media library.

1. Create a presentation and upload or drop files into the media library, or
   add a YouTube link. Add media using its **Add** button or drag it to the timeline.
2. Arrange the **Picture**, **Text** and **Audio** lanes. Pictures can be images,
   uploaded videos, YouTube excerpts or black cues. Text overlays and audio
   play independently above/alongside the picture. Gaps are black/silent.
3. Click a cue to select it; double-click to focus its properties for exact start, duration, media in point, volume and
   transitions. Choose **Apply cue changes**, then **Save presentation**.
   Drag cues horizontally to move them. Partially overlap two cues in a lane
   for a crossfade lasting exactly the overlap. Three-way overlaps, identical
   starts and fully contained cues are rejected. Staggered timeline blocks
   keep overlapping cues selectable.
   Drag the small top-corner handles to adjust fade-in/fade-out lengths;
   arrow keys adjust a focused handle by 0.1s (Shift: 1s), Home/End set
   zero/full duration. Handle edits support Undo/Redo. Overlap crossfades take
   precedence over entrance/exit fades at that join.
   Add text at the preview position and black cues at the end of the picture lane.
   Click/drag the timeline ruler or orange playhead, or use the **Preview
   playhead** slider (also keyboard-accessible), to scrub. Scrubbing pauses
   local preview; **Play preview** starts from that point, without moving the
   live audience. The playhead follows playback and respects timeline zoom.
   Select a video, YouTube or audio cue for its percentage **Volume** slider
   and **Mute this cue** checkbox. Muting preserves the chosen volume.
   Apply and save to keep these per-cue settings for the audience.
   **Undo/Redo** restore up to 100 applied timeline/title edits during this
   editor session, including after saving. They do not undo library uploads
   or deletions; deleting media clears edit history. Unapplied cue fields
   retain the browser's normal text undo. Outside fields use Ctrl/Cmd+Z,
   Ctrl/Cmd+Shift+Z or Ctrl+Y.
   **Duplicate cue** appends a copy to the same lane. **Split at playhead**
   makes two cues with continuous media in points, moving a manual hold to
   the second half. The first retains its entrance (clamped to its new
   duration); the second retains its exit, with a cut between the halves.
   Both halves must be at least 0.1 seconds long.
   Snapping aligns either end of a dropped cue with nearby cue edges or the
   playhead (within eight screen pixels). Untick **Snap** or hold Shift while
   dropping to bypass it; numeric edits remain exact.
4. Choose automatic progression or **Hold until Next or Play** on picture cues.
   Holds pause all three lanes. Overlapping picture/text cues crossfade while
   both sources keep playing; audio overlaps crossfade their volumes.
   Picture/text crossfades at adjacent, non-overlapping joins remain supported
   and use the outgoing picture's frozen final frame.
5. Use local preview, then **Open controller**. Share **Open audience view**
   and choose **Start broadcast**. The controller provides Next/Previous,
   pause/play, seeking, cue jumps and blackout (picture and sound).
   Its monitor is muted unless **Hear media on this control device** is enabled.
   The controller also shows **Up next** and a **Slide navigator** thumbnail
   grid for all picture/text cues, including earlier slides. Current view,
   Up next and the scrollable grid sit side by side on desktop and stack
   on smaller screens. Selecting a
   thumbnail jumps to that cue and plays all timeline layers from there.
   Current and past slides are marked; the audience sees none of these controls.
   Uploaded videos use a paused video thumbnail; YouTube uses its provider thumbnail.
6. For a logo/holding screen, upload an image in the editor, reload the
   controller, select it under **Placeholder image** and choose **Use selected
   image**. The choice is saved per presentation. **Show placeholder / pause**
   displays only that image and pauses/silences every timeline layer.
   **Play / resume** returns to the same point, or choose a thumbnail to jump
   elsewhere. Blackout still overrides the placeholder. Clear the selection
   before deleting a placeholder asset from the library.

Each presentation has its own persistent, anonymous audience link, suitable
for one projector or several devices. Only its owning admin can edit or
control it. **Replace audience link** disconnects viewers and invalidates the
old link. Deleting a presentation also invalidates its link. Saving edits does
not alter a running broadcast. Before broadcasting, the controller previews
the latest saved cues and automatically reloads saved edits. During a broadcast,
it explicitly flags newer saved changes (including changes in linked concert
sets). **Load saved changes / restart broadcast** loads the latest version
from the beginning after confirmation. Cancelling leaves the live show alone.
Leave the controller open and connected; missing controller heartbeats blank
the public view after eight seconds. Audience network failures also blank
playback, with automatic reconnection while the link remains valid.

### Linking music and visual cues

For a standalone set, open **Music links** in its editor. Choose one of your
saved click tracks, or **Create click track for this presentation**. Edit its
sections in the Click Tracks tab, then **Reload tracks / review changes**.
Add events at a section, bar within that section and beat (a meter unit:
1-6 in 6/8, not necessarily six audible clicks). Each event selects a picture
or text cue and either runs the visual timeline from there or holds it there.
Save the presentation before broadcasting. At least one event is required.

An image can therefore stay held across several musical sections, while
several events within one section can select different images. These are
links between two timelines, not a requirement for one slide per section.
Selecting an event jumps all three visual timeline lanes to the chosen
cue's start or its video-local hit time. A held cue cuts its entrance transition
so it remains visible; held video freezes on that hit and timeline sound stops.

For video/YouTube cues, set **Video-local hit time** in seconds from that
source video's own zero. Leave it blank to use the cue's in point. For example,
a cue at timeline 20s with source in point 60s maps source hit 65s to timeline
25s; another video's source zero is completely independent. Scrub the editor
preview inside the selected cue and choose **Use preview playhead** to capture
a hit, or type the source time and use **Preview this hit**. **Edit event**
changes an existing link. Splitting a video cue moves later source-time hits
to the new excerpt; undo restores both. Hits must lie inside the saved excerpt.
These links synchronize existing musical sections to the chosen video times;
they do not automatically refit tempos. The existing Click Tracks video
hit-point builder remains the tool for calculating music from a film's timings.

Start the broadcast in the presentation controller, then start the embedded
click-track transport. Its normal automatic section changes, keyboard/MIDI
pedals and rehearsal controls drive the linked visual events. Configure and
focus the embedded transport for keyboard pedals. Controller Play, Pause,
Next and Previous also route to that transport. Mapped slide thumbnails
rehearse their associated musical positions; unmapped thumbnails and numeric
visual seeking are unavailable during a linked broadcast.

For each silent manual musical wait, choose **Pause visuals with music** or
**Let visuals keep running**. Transport Pause always pauses visuals; manually
repeating click sections keep running until advanced. **Show placeholder /
pause** pauses the transport as well as visual media; controller Play resumes.
Click-track audio is local to the conductor's device, not sent to audience
devices. Video/audio cue settings still govern audience media sound.

The broadcast freezes both saved music and visual cues. Changing musical
sections requires reviewing event positions before saving/restarting, so
reordering a score cannot silently relink the wrong sections. Reloading the
controller pauses a running visual timeline: select a mapped rehearsal point
or deliberately restart music rather than expecting seamless audio takeover.
A lost embedded transport pauses visuals after four seconds.

Concert programmes compile linked set music into one read-only performance
track, preserving each set's event positions, tempi, meters, repeats and
pause policies. Nested/repeated sets receive distinct cue/event IDs. Intervals
become silent musical waits showing the concert logo: one **Next** or pedal
advance starts the following set, including its configured count-in.
Visual playback is bounded by the current set, so a shorter film cannot
run into the next set before its music starts. The expanded concert supports
up to 5000 musical sections/events as well as the existing visual limits.

A concert with no linked music behaves as before. In a mixed concert, a set
without linked music runs its visual timeline silently, then waits for Next;
Next enters the interval and a further Next starts the following set. Manually
repeating or silent musical sections inside linked sets retain their own
pedal waits. Timed musical pauses must be followed by a click section within
the same set. All music is compiled from saved versions at broadcast start,
not modified in the original tracks.

### Concert programmes and linked sets

Create each set as its own presentation. Create an empty presentation for the
whole concert, tick **Concert programme**, add the saved sets in running order
and select an uploaded **Concert interval placeholder** image. Use Move up/down
to reorder, Remove to unlink, or a set's title to open its editor in another tab.
Save, then **Load saved concert preview** to rehearse locally, or open the
controller and start the concert at its own persistent audience link.

The concert uses the latest saved version of every linked set at broadcast
start; the running snapshot never changes underneath viewers. Sets remain
independently editable and can also link other programmes (no cycles, maximum
eight nesting levels). A concert can link up to 50 presentations; the fully
expanded show must fit within 24 hours and 5000 cues. Linked sets must contain
saved cues; concerts cannot mix their own timeline cues with linked sets.

Between sets the timeline shows the concert's image and holds all three lanes
until **Next** or **Play / resume**. The interval occupies a nominal 0.1 seconds
in the timeline, but holds indefinitely in real time. An end-of-set manual
hold is replaced by this interval so it takes only one Next to start the next
set. Internal holds within sets are preserved. The slide grid labels cues with
their set names and includes interval slides for direct navigation.
Remove set links before deleting a set; unlink/replace interval images before
deleting them. Pre-show checks compile all saved linked sets. Editor preview
must be reloaded to pick up changes saved in another set.

Use **Pre-show checks** in the editor or controller to load each used media
asset and check excerpt bounds where the player reports duration. Checks are
local, read-only and cancellable: they do not start a broadcast or guarantee
full-file buffering. Editor checks use applied edits, controller checks use
the latest saved version. **Test this device's sound** sends a short tone;
confirm that you hear it. Re-run checks after edits and rehearse on every
audience device to check its autoplay permissions, sound output and network.
YouTube duration may not be available until playback, which is reported as
a warning rather than a verified excerpt.

### Media storage and playback requirements

- Configure `CLOUDINARY_URL` on the server. No local-disk upload fallback is
  used. Uploads go directly from the browser to Cloudinary using a short-lived,
  admin-authorized signature; the server verifies the stored media before
  adding it to the library. Cloudinary credentials stay on the server
  (only the public API key and upload signature go to the browser).
- Files are limited to **100 MB each**; uploaded videos must be **three
  minutes or less**. Your Cloudinary plan may impose lower limits. Supported
  inputs: JPG/JPEG, PNG, WebP, MP4, WebM, MOV, MP3, WAV, M4A and OGG.
  Browser codec support still applies; MP4/H.264 and MP3 are recommended.
  YouTube cues have no three-minute limit: use any excerpt or full video
  that fits in the existing 24-hour timeline, subject to the source video's
  actual length. This does not change the three-minute uploaded-video limit.
- Media URLs are public, not private or access-controlled. Revoking a
  presentation link does not revoke already shared media URLs.
  Deleting presentations retains library media. **Delete media** permanently
  removes unused uploads from Cloudinary; saved or running timelines must
  release them first. Interrupted/unfinalised uploads may need removing
  manually from the `jackcapstaff/presentations` folder in Cloudinary.
- Browsers may require **Allow media playback** on each audience device
  before sound/video starts. Video and YouTube cues default muted; untick
  **Mute this cue** to enable their sound. Independent audio defaults on.
- This is online, approximate wall-clock synchronization, not frame-accurate
  broadcast playout. YouTube requires connectivity, allows only embeddable
  videos, and may buffer or show provider content. Rehearse on the intended
  devices/network. There are no waveform/thumbnail trim tools or offline
  presentation export in this first version.

## Click Tracks

The Click Tracks tool is available at `/live/` (or `/live`, which adds the trailing
slash). Existing `/clicktrack/` URLs remain supported, including APIs, prepared
offline pages and unexpired legacy audience links. Saved tracks and backups
remain compatible.

Only administrators can create, edit, duplicate, import or delete performance tracks
and video drafts. In Edit, tick **Public track** to make a saved track available
to all signed-in users for performance; untick it to make it private again.
Existing and newly created tracks are private by default, including duplicates
and restored backups. Admins continue to manage their own tracks.
Non-admin users (including the editor role) see only public tracks owned by
admins and can select, rehearse and perform them, with video and presentation
controls, but cannot change the saved music. Their pedal, sound-output and
display preferences remain personal. Only admins can create or broadcast
public live links; ordinary performers can still use the local presentation
window. The API enforces the same restrictions.
Public-track selection still requires login; the audience's public live-video
link described below does not. Unpublishing removes access on the next load;
it cannot retract a previously downloaded backup or prepared offline show.

To invite band members for independent practice, publish the track, then choose
**Track actions → Invite band members**. Copy the invitation link, or use the
displayed link if clipboard access is unavailable. The `/live/invite/<track-id>/`
page offers signup and login and preserves the invited track through both.
It opens that track in read-only Perform mode rather than the member's last
track, with video enabled and film audio on. Each member presses Start and
controls their own playback (browsers may require interaction before sound).
This is not the live audience broadcast. Invites do not grant private-track
access; unpublishing or deleting the track makes its invite unavailable.

In the signed-in Performance Studio page, open **Track actions → New performance track from
video** as an administrator. The same dropdown has the new track and JSON options, and **Pedal
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

For a timing jump partway through a replacement video, select the first
affected hit point and find **Move hit points in time**. Under **What should
move?**, select **Ripple edit — this hit point and everything after it**.
Change its **Video time**, for example from `10:00.000` to
`10:00.500`: that point and all later musical points move 0.5 seconds later.
Their spacing stays unchanged; earlier points and video 0:00 stay fixed.
The preceding section's tempo is recalculated to meet the moved point.
Negative changes move the remainder earlier. Ripple also applies to playhead
moves, nudges and timeline dragging; **Just this hit point** is the default.
Section duration and video-start tools are grouped separately and are not
affected by the ripple setting.
Invalid order, video limits or impossible tempi are rejected without applying
the ripple. Linked tracks use the existing automatic draft/track saving.

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

The **Find section / rehearsal points** panel stays available in both normal
and fullscreen performance views. Use it to pause and restart
at a section or score bar.
Bar, meter and tempo figures automatically shrink to fit their columns without
wrapping, including beside video, in fullscreen and in the editor preview.

For an audience display, enable video in Perform and choose **Open presentation
window**. Move that window to the audience display and make it fullscreen using
the browser's fullscreen controls. It shows only the video, without streamers
or punches; playback follows the conductor window. Choose **Conductor window**
or **Presentation window** for film audio and enable **Play film audio**.
Allow pop-ups and, if prompted, allow playback in the presentation window.
YouTube seeking and synchronization are approximate; check both windows before
performing. Closing the presentation window returns film audio to the conductor
window. The presentation closes when you leave Perform or disable video.

For viewers on other devices, as an admin choose **Create public live link** in Perform,
then **Copy public link**. The separate `/live/watch/YYYY-MM-DD/` URL
needs no login and follows your live start, pause and seek controls. Only the
video and playback state are public, not your track, notes or hit points.
This supports saved YouTube videos, not files local to your computer.
The date uses **Europe/London**, including British Summer Time. There is one
shared broadcast per date across all admins. Creating a second broadcast
requires explicit confirmation to replace the current one; old conductor
updates cannot control its replacement. The daily URL is predictable and
intentionally public, not a secret link. Stopping sharing takes it offline;
starting again that day reuses the same URL. Viewers whose page already says
the broadcast ended must reload it. Date changes do not redirect yesterday's
viewers; share the new day's link when starting a new broadcast.
The admin controls remote film audio using **Play film audio on audience
devices**, off by default. It applies live to every viewer, including after
a viewer reloads; the public page has no audio toggle. Conductor and local
presentation audio settings stay independent. Viewers may still need to allow
playback in their browser, particularly when the admin enables sound.

Admins can adjust **Audience timing offset (seconds)** during playback.
If the audience video is behind, try `+0.50` to move it ahead half a second;
negative values delay it. This updates everyone watching that live link on
their next poll without changing music, hit points or the local presentation.
It applies only while playing, never to paused seeks, pre-video blackout or
stale connections. The range is -10 to +10 seconds, default 0 on page load;
it is not saved with the track. Network-delay estimation is already automatic,
but YouTube buffering and individual screen delay cannot be measured reliably.
A shared offset cannot guarantee frame-accurate sync across different devices.

Keep the conductor page open with internet access. Cross-device timing is
approximate (updates are polled about once a second); buffering, network delays,
ads and YouTube seeking can cause differences, so use the local presentation or
a wired display for critical projection. The public picture remains black
before video 0:00 and goes black if live updates stop for about five seconds.
When music starts before video 0:00, the picture fades up over 0.8 seconds as
it begins, in the conductor, local presentation and public audience views.
Returning to a pre-video position or losing the live connection blacks out
immediately; starting or seeking later into the film does not add a fade.
The same link resumes after a temporary connection loss. **Stop public sharing**,
changing tracks or leaving Perform stops the broadcast. Broadcasts expire after
12 hours; after reloading the conductor or expiry, start sharing again (confirm
replacement if asked). The same date reuses its URL; a new date gets a new URL.
Anyone can discover and view a daily URL while active. Public playback cannot
control the conductor.
The relay uses the shared application database, so it works across server workers;
the new table is created by the application's existing `db.create_all()` startup.

To start the music before the picture, select the musical hit point that should
coincide with the start of the video and choose **Start video at this hit
point**. This shifts all musical hit-point times by the same amount, preserving
their spacing; earlier points receive negative times. Check the shifted points
against the video afterwards, especially when replacing the film. Edit negative
times in the hit-point list; they cannot be sought to in the video itself.
The normal sections and their meters, tempi and score bars remain in use. In
Perform, the count-in comes first, then the pre-video music; the conductor and
presentation videos wait at 0:00 until the selected hit point. Starting from a
later section or using **Start video from beginning** follows the same alignment.
Manual cue waits or repeats can make planned times approximate.
The presentation picture stays black while the selected performance position
is before video 0:00, and reappears when the film begins.
In Perform, playing or pausing the conductor's video also plays or pauses the
performance track. Seeking the video moves the metronome to the corresponding planned
position without a new count-in, preserving whether playback was paused or
running. After a manual cue or repeat, the planned position assumes one pass and
no waiting; the video status warns that the position is estimated. Use the
section finder for an exact rehearsal restart. YouTube controls and seeking are
approximate; the presentation window follows the conductor.
Keep the video playback rate at normal speed; changing it does not change the
click-track tempo.
In the video editor, select a negative-time hit point and choose **Start from
hit point** to preview from its section count-in. Enable **Preview click with
video** to hear the count-in and music; the video waits at 0:00. The existing
pause-before-hit option also applies before the picture starts.

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
has a valid timing plan, a linked performance track is created automatically and
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
If the track does not yet have a linked draft, open a saved draft in the video
editor and choose **Link open draft to track**. An open draft alone is not
available in Perform. If you replace its video before linking, link it and wait
for the save to finish before switching to Perform.
Duplicating a linked track retains its video link; subsequent linked-draft edits
are saved separately so they do not change the original track's draft.
To change the duration leading into a hit point, select the preceding musical
hit point and use **Duration to next hit point**. Earlier musical spacing and
the following point's video time stay fixed; the selected section's tempo is
recalculated. For a fine adjustment without changing musical spacing, use
**Start video earlier** or **Start video later** in **Resync hit-point times**.
The adjustment moves all musical hit-point times relative to video 0:00.
For versions with different running speeds, use **Adjust timing for a different
video speed**. Preview and apply a percentage anchored at video 0:00: 101%
makes all intervals 1% longer, and 99% makes them 1% shorter. Musical tempi,
including fixed ramp anchors, are recalculated and timed pauses are scaled.
This changes the saved hit-point timing, not YouTube's playback speed. Each
application scales the current timing again; the preview shows the first and
last resulting hit points. Times are rounded to milliseconds.

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
