MANAGED SERVICES DASHBOARD - HOW TO UPDATE
==========================================

1. Put the new files in the "Drop files here" folder.
   - The main workbook (any file name; it is found by its "Managed Services Details" sheet).
     If there are several, the most recently saved one is used. You can leave old ones or delete them.
   - Time-study workbooks (any file with a "Time Study" sheet), in any sub-folder.
     A file with the same name as an older one replaces it.
   - The time-study synopsis (the file with a "Metrics-Overall" sheet).
   - A .zip of any of the above works too; it is unzipped automatically.

2. Double-click "Refresh Dashboard.bat".
   "Managed Services Dashboard.html" is rebuilt and opens in your browser.
   All filters, sliders and what-if scenarios work in the browser, with no internet needed except for fonts.

   Or run "Auto-Refresh (watch folder).bat" and leave its window open: the dashboard rebuilds
   a few seconds after any file in the drop folder changes. Reload the browser tab to see it.

WHAT UPDATES AUTOMATICALLY
- Months: the month list grows with the data (for example Jan-Nov after a November close).
- Ticket tab: uses the newest month that has sheets named "<Month> Help Desk", "<Month> Installs"
  and "<Month> Projects" (for example "October Help Desk").
- Time studies: new rounds and new people are picked up from the file name ("Round 3", "RoundTwo").
  A person not recognized from the file name is shown under the file name. Tell Claude the name to add.
- Data quality checks are recalculated on each refresh. Missing sheets and anything skipped are listed
  there as "Refresh note" items and in "last refresh log.txt".

OTHER FILES
- history\    a dated copy of every refresh (last 30 are kept)
- engine\     the build scripts and the page template (needs Python 3.12 with pandas and openpyxl)

PRIVACY
Requester names, end-customer company names, ticket subjects, serial numbers and time-study
notes are never read into the dashboard. The HTML file contains bank names, staff names and
financials, so share it only with people who should see those.
