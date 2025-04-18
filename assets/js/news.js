// news.js
const newsContainer = document.getElementById('newsContainer');

const stories = ['story1.txt', 'story2.txt', 'story3.txt']; // List of your story files

stories.forEach(file => {
    fetch(`https://raw.githubusercontent.com/JackCapstaff/jackcapstaff.com/main/assets/${file}`)
        .then(response => {
            if (!response.ok) {
                throw new Error('Network response was not ok: ' + response.statusText + ' for ' + file);
            }
            return response.text();
        })
        .then(data => {
            const lines = data.split('\n');
            if (lines.length > 0) {
                const title = lines[0].replace('Title: ', '').trim(); // Get the title from the first line
                const content = lines.slice(1).join('<br>').trim(); // Join the remaining lines for content

                const storyElement = document.createElement('div');
                storyElement.innerHTML = `<h2>${title}</h2><p>${content}</p>`;
                newsContainer.appendChild(storyElement); // Add the story to the news container
            }
        })
        .catch(error => console.error('Error fetching story:', error));
});