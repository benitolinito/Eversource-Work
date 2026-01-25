# Eversource-Work (DALI App outside code sample)

## Eversource User Statistics Page

The code sample can be found in the 'user_statistics.html' file of this repo.

I was tasked with creating a user statistics page to help Evergreen project managers track student work and progress creating dialogue training data on Eversource (a fullstack Flask web app that will store LLM training data for the Evergreen generative chatbot model). 

### Features:
1. User table that tracks:
  - User, Team, Dialogues Created, Reviews Done, Average Rated Reviews, Average Rating Received
  - Search/sort table by Name, Team, or Start/End Date
2. Graph Actions:
  - View Reviews, View Dialogues, View Graph
3. Overview of Team Performance


### Implementation:
- Frontend/UI implemented with Flask/Jinja template with Bootstrap layout (HTML, CSS, Javascript) (as per structure of Eversource project).
- HTML for user table
- Chart.js library used for graph trends


## Learning:
- This is one of the first times I had to use javascript and familiarize myself with syntax, ect.
- This was also one of my first times integrating stored backend data with front end (backend local API calls)
- It was also an interesting process to go through pre-existing code and trying to utilize/understand how everything fits together.
- Collaborative process meeting with team to discuss the implementation of this page


Note: I did use AI to help generate code for the HTML and UI elements. Our team valued speed and efficiency, and this allowed us to push updates out faster. However, I primarily used it to understand our project structure/design/layout, formatting code, understanding javascript, and debugging.



## Screenshots

<img src="Graph stats.png">
<img src="statistics dashboard.png">
<img src="team performance.png">
<img src="user table.png">
