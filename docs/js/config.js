// Site settings. Edit these after deploying the Cloudflare Worker.
export default {
  // Your deployed worker, e.g. "https://mangadle-api.yourname.workers.dev".
  // Leave empty to play without shared stats or suggestions.
  workerUrl: "",

  // Every player gets the puzzle for the current date in this time zone.
  timezone: "America/Chicago",

  // Link included in the share text. Empty = the page's own address.
  siteUrl: "",
};
