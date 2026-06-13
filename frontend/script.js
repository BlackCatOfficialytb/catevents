// Replace with your actual Hugging Face Dataset Raw URL inside this variable:
const HF_DATA_URL = "https://huggingface.co/datasets/civil384/scraped-trends-database/raw/main/trends.json";

// Map selectors to their dynamic components
const SELECTORS = {
    reddit: "reddit-trends",
    x: "x-trends",
    google: "google-trends",
    tags: "hot-tags",
    status: "status-badge",
    notice: "demo-notice",
    refreshBtn: "refresh-btn",
    closeNoticeBtn: "close-notice-btn"
};

// Fetch trends and handle loading status update
async function fetchTrends() {
    updateStatusBadge("Connecting...", "bg-yellow-900 text-yellow-300 animate-pulse");
    renderSkeletons();

    try {
        // Prevent useless network requests if placeholder variable is unconfigured
        if (HF_DATA_URL.includes("YOUR_USERNAME")) {
            throw new Error("Backend placeholder detected");
        }

        const response = await fetch(HF_DATA_URL);
        if (!response.ok) throw new Error(`HTTP status error: ${response.status}`);
        
        const data = await response.json();
        renderData(data);
        updateStatusBadge("Live Data Connected", "bg-green-900 text-green-300");
        document.getElementById(SELECTORS.notice).classList.add("hidden");
    } catch (err) {
        console.warn("Could not retrieve Hugging Face data. Backend is down or unconfigured. Error details:", err);
        
        // Show status code 400 or generic offline error
        updateStatusBadge("Offline (Error 400)", "bg-red-900 text-red-300");
        
        // Render dynamic error states inside list views
        renderErrorStates();

        // Display notice alert block pointing to the server status domain
        const noticeEl = document.getElementById(SELECTORS.notice);
        if (noticeEl) {
            noticeEl.className = "bg-red-950 border border-red-800 text-red-300 px-4 py-3 rounded-lg text-sm flex justify-between items-center";
            noticeEl.innerHTML = `
                <span class="flex-1 pr-4">
                    ⚠️ <strong>Backend Servers/Database not found/is down</strong>, please see 
                    <a href="https://status.blackcatofficial.qzz.io" target="_blank" rel="noopener noreferrer" class="underline text-white font-bold hover:text-red-200">status.blackcatofficial.qzz.io</a> 
                    to check server status.
                </span>
                <button id="${SELECTORS.closeNoticeBtn}" class="text-red-400 hover:text-white text-lg font-semibold leading-none">&times;</button>
            `;
            noticeEl.classList.remove("hidden");

            // Re-bind click listener for the newly injected close button
            document.getElementById(SELECTORS.closeNoticeBtn)?.addEventListener("click", () => {
                noticeEl.classList.add("hidden");
            });
        }
    }
}

// Render dynamic data values to lists and flex sections
function renderData(data) {
    renderColumn(SELECTORS.reddit, data.reddit, "text-orange-400");
    renderColumn(SELECTORS.x, data.x, "text-blue-400");
    renderColumn(SELECTORS.google, data.google, "text-red-400");
    renderTags(data.macro_trends);
}

// Render specific platform list structures
function renderColumn(elementId, items, colorClass) {
    const container = document.getElementById(elementId);
    if (!container) return;

    if (!items || items.length === 0) {
        container.innerHTML = `<li class="text-gray-500 italic p-2 text-center">No trend entries found</li>`;
        return;
    }

    container.innerHTML = "";
    items.forEach((item, index) => {
        const li = document.createElement("li");
        li.className = "flex justify-between items-center p-2 rounded hover:bg-gray-800 transition duration-150 ease-in-out";
        
        const safeTitle = escapeHTML(item.title);
        const safeScore = escapeHTML(item.score);

        li.innerHTML = `
            <span class="truncate pr-2">
                <strong class="text-gray-500 mr-2 font-mono">${index + 1}.</strong>${safeTitle}
            </span>
            <span class="text-xs font-mono ${colorClass} bg-opacity-20 bg-gray-900 border border-gray-800 px-2.5 py-0.5 rounded-full whitespace-nowrap">
                ${safeScore}
            </span>
        `;
        container.appendChild(li);
    });
}

// Render hot macro tags inside the tag container
function renderTags(tags) {
    const tagsContainer = document.getElementById(SELECTORS.tags);
    if (!tagsContainer) return;

    if (!tags || tags.length === 0) {
        tagsContainer.innerHTML = `<span class="text-gray-500 italic text-sm">No recent tags</span>`;
        return;
    }

    tagsContainer.innerHTML = "";
    tags.forEach(tag => {
        const span = document.createElement("span");
        span.className = "bg-blue-950 text-blue-300 hover:text-white px-3 py-1 rounded text-sm font-mono border border-blue-800 hover:border-blue-600 transition cursor-pointer";
        span.textContent = tag;
        tagsContainer.appendChild(span);
    });
}

// Reset standard sections with animated placeholder items during fetching
function renderSkeletons() {
    const defaultSkeletons = Array(4).fill(null).map(() => 
        `<li class="skeleton w-full h-9 rounded"></li>`
    ).join("");

    const tagSkeletons = Array(3).fill(null).map(() => 
        `<div class="skeleton w-20 h-7 rounded"></div>`
    ).join("");

    document.getElementById(SELECTORS.reddit).innerHTML = defaultSkeletons;
    document.getElementById(SELECTORS.x).innerHTML = defaultSkeletons;
    document.getElementById(SELECTORS.google).innerHTML = defaultSkeletons;
    document.getElementById(SELECTORS.tags).innerHTML = tagSkeletons;
}

// Clear lists out and render generic offline messages when data connections fail
function renderErrorStates() {
    const offlineMessage = `<li class="text-gray-600 italic p-2 text-center text-xs">Offline - Service soon</li>`;
    document.getElementById(SELECTORS.reddit).innerHTML = offlineMessage;
    document.getElementById(SELECTORS.x).innerHTML = offlineMessage;
    document.getElementById(SELECTORS.google).innerHTML = offlineMessage;
    document.getElementById(SELECTORS.tags).innerHTML = `<span class="text-gray-600 italic text-xs">Offline</span>`;
}

// Change state indicators
function updateStatusBadge(text, classString) {
    const badge = document.getElementById(SELECTORS.status);
    if (badge) {
        badge.className = `inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${classString}`;
        badge.textContent = text;
    }
}

// Simple HTML escaper to sanitize data strings safely
function escapeHTML(str) {
    if (!str) return "";
    return str
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

// Bind interactive click handlers when document loads
document.addEventListener("DOMContentLoaded", () => {
    fetchTrends();

    // Bind refresh button click handler
    const refreshBtn = document.getElementById(SELECTORS.refreshBtn);
    if (refreshBtn) {
        refreshBtn.addEventListener("click", fetchTrends);
    }
});