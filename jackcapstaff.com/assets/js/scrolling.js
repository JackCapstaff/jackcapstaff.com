const events = [
    {
        title: "BBCA - Jack Capstaff Conducting Workshop with Derwent Brass",
        datetime: "April 12, 2025 10:00 AM",
        image: "images/logo.png",
        link: "https://www.derwentbrass.co.uk",
    },
    {
        title: "Derby Concert Orchestra: Slavic Splendor",
        datetime: "May 17, 2025 19:00 PM",
        image: "images/logo.png",
        link: "https://www.derbyconcertorchestra.co.uk",
    },
    {
        title: "Rotherham Symphony Orchestra: ",
        datetime: "April 10, 2025 12:00 PM",
        image: "images/logo.png",
        link: "https://www.rotherhamsymphony.org",
    },
];

let currentIndex = 0;
const eventBox = document.getElementById('event-box');
const eventTitle = document.getElementById('event-title');
const eventDatetime = document.getElementById('event-datetime');
const eventImage = document.getElementById('event-image');
const eventLink = document.getElementById('event-link');

function updateEvent() {
    const event = events[currentIndex];
    eventTitle.textContent = event.title;
    eventDatetime.textContent = event.datetime;
    eventImage.src = event.image;
    eventLink.href = event.link;

    eventBox.style.transform = 'translateX(100%)'; // Start from the right side
    setTimeout(() => {
        eventBox.style.transition = 'none'; // Disable transition for the reset
        eventBox.style.transform = 'translateX(0)'; // Move to the center
        setTimeout(() => {
            eventBox.style.transition = 'transform 1s ease'; // Re-enable transition
        }, 10);
    }, 0);

    currentIndex = (currentIndex + 1) % events.length; // Change index for the next event
}

// Rotate events every 10 seconds
let intervalId = setInterval(updateEvent, 10000);
updateEvent(); // Initial call for the first event

eventBox.addEventListener('mouseenter', () => clearInterval(intervalId)); // Pause on hover
eventBox.addEventListener('mouseleave', () => {
    intervalId = setInterval(updateEvent, 10000); // Resume when mouse leaves
});