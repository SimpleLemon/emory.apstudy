# Next Steps: Post-Scrape Todo List

---

## Phase 1: Server Foundation & Auth

**Goal:** Express server boots on DBH, login works, protected routes reject unauthenticated requests.

- [ ] **Initialize the project.** Run `npm init`, create `package.json` with a `start` script pointing to `server.js`. Install core packages: `express`, `dotenv`, `express-session`, `connect-sqlite3`, `bcrypt`, `helmet`, `express-rate-limit`, `compression`.

- [ ] **Create `.env` with all required variables.** `PORT`, `SESSION_SECRET` (generate a random 64-char string), `ALLOWED_USERNAME`, `PASSWORD_HASH` (generate via a one-off `bcrypt.hash()` call in Node REPL). Leave `CANVAS_ICAL_FEED_URL` and `ICS_SECRET_TOKEN` blank for now since you won't have your Canvas iCal feed URL until fall enrollment.

- [ ] **Write `server.js` entry point.** Load dotenv, configure Express with `helmet()`, `compression()`, `express.json()`, `express.urlencoded()`, static file serving from `/public`, session middleware backed by `connect-sqlite3`. Mount route files. Listen on `process.env.PORT`.

- [ ] **Write `/middleware/requireAuth.js`.** Single function: check `req.session.authenticated === true`, otherwise redirect to `/login.html` or return 401 for API routes.

- [ ] **Write `/routes/auth.js`.** POST `/login` validates username against `ALLOWED_USERNAME` and password against `PASSWORD_HASH` using `bcrypt.compare()`. On success, set `req.session.authenticated = true` and redirect to `/dashboard.html`. POST `/logout` destroys session. Apply `express-rate-limit` (e.g., 5 attempts per 15 minutes) to the login endpoint specifically.

- [ ] **Write `login.html` and a placeholder `dashboard.html`.** Login page needs a simple form (username + password fields, submit button). Dashboard can just say "Authenticated" for now. Both in `/public`.

- [ ] **Test locally.** `node server.js` should boot, login should create a session, dashboard should be protected, and logout should kill the session. Confirm that refreshing after logout redirects to login.

---

## Phase 2: Deploy to DBH

**Goal:** Server runs on Pterodactyl, accessible via `canvas.apstudy.org` over HTTPS.

- [ ] **Create a new server on DBH using the Node.js egg.** Confirm the Node version is 18 or higher. Upload your project files (or use Git if DBH supports it).

- [ ] **Verify the assigned port.** Pterodactyl will set `process.env.PORT` automatically. Confirm your Express server binds to it, not a hardcoded value.

- [ ] **Test outbound HTTPS from DBH.** Before building any external API calls, SSH into the container (or add a temporary test route) and confirm `axios.get('https://atlas.emory.edu')` resolves. Some Pterodactyl configurations restrict outbound networking [1]. If blocked, you'll need to contact DBH support or switch hosts.

- [ ] **Configure Cloudflare DNS.** Add an A record for `canvas.apstudy.org` pointing to your DBH server IP. Enable Cloudflare's proxy (orange cloud) for automatic HTTPS termination. Set SSL mode to "Full" (not "Full Strict" since DBH likely doesn't have its own certificate).

- [ ] **Verify end-to-end.** Open `https://canvas.apstudy.org` in a browser. You should see the login page served over HTTPS. Log in, confirm session persists, log out.

- [ ] **Test container restart resilience.** Restart the server from the Pterodactyl panel. Confirm that `connect-sqlite3` preserves your session database file and that the server comes back online without manual intervention.

---

## Phase 3: Atlas Data API

**Goal:** Serve your scraped Atlas course data through backend API endpoints that the frontend can consume.

- [ ] **Upload `atlas-data/` to the DBH server.** Place it inside your project directory. This is static data from your one-time scrape, so it just needs to be readable from disk.

- [ ] **Write `/routes/atlas.js` with three endpoints:**

  - `GET /api/atlas/subjects` returns a list of all subject folders (e.g., `["BIOL", "CHEM", "CS", "MATH", ...]`) by reading the directory listing from `atlas-data/Fall_2026/`.

  - `GET /api/atlas/courses/:subject` returns a list of all course files in that subject folder (e.g., `["141", "141L", "142"]` for BIOL) by reading the filenames.

  - `GET /api/atlas/course/:subject/:catalog` returns the full JSON content of a specific course file (e.g., `atlas-data/Fall_2026/BIOL/141.json`).

  All three endpoints should be behind `requireAuth` middleware.

- [ ] **Add a term query parameter.** Support `?term=Fall_2026` and `?term=Spring_2026` on each endpoint to switch between semesters. Default to `Fall_2026`.

- [ ] **Add basic in-memory caching.** Reading JSON files from disk on every request is fine for a single user, but caching the directory listings (subjects and course lists) in a simple object avoids unnecessary `fs.readdir` calls. Course file content can be read on demand since individual files are small.

- [ ] **Test all three endpoints** via browser or `curl` while authenticated. Confirm JSON responses are well-formed and the term switching works.

---

## Phase 4: Dashboard Frontend (Atlas Course Browser)

**Goal:** A functional course search and browse interface on the dashboard.

- [ ] **Design the dashboard layout.** Two-panel structure: a sidebar or top bar for navigation (course browser, calendar, settings) and a main content area. Start with the course browser only.

- [ ] **Build the course browser in `dashboard.js`.**

  - On load, fetch `/api/atlas/subjects` and populate a subject dropdown or searchable list.
  - On subject selection, fetch `/api/atlas/courses/:subject` and display course numbers as clickable items.
  - On course selection, fetch `/api/atlas/course/:subject/:catalog` and render the full course detail: title, sections table (with columns for CRN, section number, type, instructor, meeting times, enrollment status), and the `instructors_unique` and `section_summary` fields [1].

- [ ] **Add a direct search bar.** Let the user type something like "CHEM 150" or "BIOL 141" and jump directly to that course by parsing the input into subject + catalog and fetching the file. This avoids the two-step dropdown flow for known courses.

- [ ] **Style with `/css/style.css`.** Clean, minimal layout. Color-code enrollment status (green for Open, red for Closed, yellow for Waitlist). Use your preferred theming approach. Make the sections table readable on mobile since this will eventually be a PWA.

- [ ] **Add a term toggle.** A simple dropdown or toggle button at the top of the course browser that switches between Fall and Spring 2026, re-fetching data from the corresponding term folder.

---

## Phase 5: Canvas Calendar Integration

**Goal:** Fetch and display Canvas calendar data alongside Atlas course data.

This phase is **blocked until fall** because you need an active Emory Canvas account to obtain your iCal feed URL. But you can prepare the infrastructure now.

- [ ] **Write `/services/feedFetcher.js`.** Uses `axios` to GET the Canvas iCal feed URL from `.env`. Parses the response with `node-ical` into structured JavaScript objects. Stores the parsed result in a cache file (`/data/calendar-cache.json`) or SQLite. Runs on a `node-cron` schedule (configurable via `FEED_REFRESH_INTERVAL_MINUTES`).

- [ ] **Write `/routes/calendar.js` with two endpoints:**

  - `GET /api/calendar/events` returns the cached parsed events as JSON (for the dashboard to render).
  - `GET /calendar.ics?token=YOUR_SECRET` serves a rebuilt .ics file via `ical-generator` for Apple Calendar subscription. The `token` query parameter is checked against `ICS_SECRET_TOKEN` in `.env` since Apple Calendar can't send session cookies.

- [ ] **Build a calendar view in the dashboard.** This can be a simple list of upcoming assignments sorted by due date, or a month/week grid view if you want to invest more time. The calendar view should pull from `/api/calendar/events`.

- [ ] **Merge Atlas schedule data into the calendar.** If you've selected courses you're enrolled in (a "My Courses" feature, see Phase 6), their meeting times from Atlas can be rendered as recurring events alongside Canvas assignment due dates. This is where the two data sources become genuinely useful together.

- [ ] **Subscribe from Apple Calendar.** Add `https://canvas.apstudy.org/calendar.ics?token=YOUR_SECRET` as a subscribed calendar on your iPhone. Verify that events appear and auto-refresh.

---

## Phase 6: UX & Personalization

**Goal:** Make the dashboard feel like a personal tool, not a data browser.

- [ ] **Add a "My Courses" selection.** Let yourself mark courses as enrolled. Store the selection in a small SQLite table or a JSON file. This selection drives which Atlas courses appear in the calendar view and which Canvas events are highlighted.

- [ ] **Course filtering and toggles.** Show/hide courses by subject, schedule type (lectures vs. labs), or enrollment status. Persist filter preferences in the session or a settings file.

- [ ] **Configurable refresh interval.** A settings page where you can change the Canvas feed refresh frequency and see when the last fetch occurred.

- [ ] **PWA manifest and service worker.** Add `manifest.json` with `name`, `short_name`, `start_url: /dashboard.html`, `display: standalone`, and icon references (192px + 512px). Register a minimal `service-worker.js` that caches the app shell (HTML, CSS, JS) for offline loading. This enables "Add to Home Screen" on iOS via Safari.

- [ ] **Responsive design pass.** Test the dashboard on your iPhone's viewport width. Ensure the course browser, calendar, and settings are all usable on mobile.

---

## Phase 7: Re-Scrape Strategy & Maintenance

**Goal:** Keep Atlas data fresh without manual effort.

- [ ] **Build a re-scrape trigger.** Add a protected endpoint (e.g., `POST /api/atlas/rescrape`) that runs the bulk scraper on demand. This lets you refresh the Atlas data when a new semester opens or when course schedules change, without SSH-ing into the DBH container.

- [ ] **Scheduled re-scrape via `node-cron`.** Atlas course data changes (sections open/close, instructors get assigned, rooms change). A weekly or daily re-scrape during registration periods keeps your data current. Outside registration season, monthly is fine.

- [ ] **Verify Spring 2026 srcdb.** Your scraper attempted `5263` for Spring. If that term wasn't active during your initial scrape, re-run once Spring 2026 registration opens and confirm the correct code.

---
