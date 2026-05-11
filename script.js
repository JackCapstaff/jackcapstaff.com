document.addEventListener('DOMContentLoaded', function () {
  var eventBox = document.getElementById('event-box');
  if (!eventBox) {
    return;
  }

  var eventLink = document.getElementById('event-link');
  var eventTitle = document.getElementById('event-title');
  var eventDatetime = document.getElementById('event-datetime');
  var eventImage = document.getElementById('event-image');

  if (eventLink) {
    eventLink.href = '/schedule';
  }

  if (eventTitle) {
    eventTitle.textContent = 'Upcoming performances and rehearsal dates';
  }

  if (eventDatetime) {
    eventDatetime.textContent = 'See the full schedule for the latest dates';
  }

  if (eventImage) {
    eventImage.src = 'images/Conducting-small.jpg';
    eventImage.alt = 'Jack Capstaff conducting';
  }
});
