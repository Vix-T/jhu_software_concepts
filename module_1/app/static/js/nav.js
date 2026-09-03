(function () {
    var STORAGE_KEY = "visitedNavPages";

    var visited;
    try {
        visited = JSON.parse(window.localStorage.getItem(STORAGE_KEY)) || [];
    } catch (err) {
        visited = [];
    }

    var currentPath = window.location.pathname;
    var links = document.querySelectorAll(".nav-link");

    links.forEach(function (link) {
        var linkPath = link.getAttribute("href");
        var isActive = link.classList.contains("active");

        if (!isActive && visited.indexOf(linkPath) !== -1) {
            link.classList.add("visited");
        }
    });

    if (visited.indexOf(currentPath) === -1) {
        visited.push(currentPath);
        try {
            window.localStorage.setItem(STORAGE_KEY, JSON.stringify(visited));
        } catch (err) {
            /* localStorage unavailable; visited-page styling is skipped */
        }
    }
})();
