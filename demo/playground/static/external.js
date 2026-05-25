// External Launchpad — records outbound navigation intent before leaving.

(function () {
  for (const link of document.querySelectorAll(".launch-list a")) {
    link.addEventListener("click", () => {
      navigator.sendBeacon(
        "/api/event",
        JSON.stringify({
          source: "external-launchpad",
          kind: "external",
          message: `opened ${link.hostname}`,
        }),
      );
    });
  }
})();
