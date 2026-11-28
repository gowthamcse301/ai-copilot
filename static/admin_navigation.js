(() => {
  const adminPaths = new Set([
    "/admin",
    "/admin/assistant",
    "/admin/channel",
    "/admin/looks",
    "/admin/inbox",
    "/admin/content",
    "/admin/settings",
  ]);
  const appShell = document.querySelector("#adminAppShell");
  const widget = document.querySelector(".global-assistant-widget");
  const launcher = document.querySelector("#assistantWidgetLauncher");
  const panel = document.querySelector("#assistantWidgetPanel");
  const panelContent = document.querySelector(".global-assistant-panel-content");
  const panelActions = document.querySelector(".global-assistant-panel-actions");
  const closeButton = document.querySelector("#assistantWidgetClose");
  const assistantConsole = document.querySelector("#ai-copilot");
  const assistantSettingsMenu = widget?.querySelector(".assistant-settings-menu")
    || document.querySelector(".admin-topbar .assistant-settings-menu");

  if (
    !appShell || !widget || !launcher || !panel || !panelContent
    || !panelActions || !closeButton || !assistantConsole || !assistantSettingsMenu
  ) return;

  let currentShell = appShell;
  let navigationSequence = 0;
  let pendingDataRefresh = false;

  function hasUnsavedFormChanges() {
    for (const control of currentShell.querySelectorAll("input, textarea, select")) {
      if (
        control.disabled || control.readOnly
        || control.closest("#ai-copilot, .assistant-settings-menu")
        || control.type === "hidden"
        || ["button", "submit", "reset", "image"].includes(control.type)
      ) continue;

      if (control.type === "file") {
        if (control.files?.length) return true;
        continue;
      }
      if (control.type === "checkbox" || control.type === "radio") {
        if (control.checked !== control.defaultChecked) return true;
        continue;
      }
      if (control.tagName === "SELECT") {
        if (Array.from(control.options).some((option) => option.selected !== option.defaultSelected)) {
          return true;
        }
        continue;
      }
      if (control.value !== control.defaultValue) return true;
    }
    return false;
  }

  function deferDataRefresh() {
    pendingDataRefresh = true;
    const notice = currentShell.querySelector("#adminDataRefreshNotice");
    if (notice) notice.hidden = false;
  }

  function refreshPendingDataWhenClean() {
    if (!pendingDataRefresh || hasUnsavedFormChanges()) return;
    pendingDataRefresh = false;
    const notice = currentShell.querySelector("#adminDataRefreshNotice");
    if (notice) notice.hidden = true;
    void navigateTo(window.location.href, {
      historyMode: "replace",
      force: true,
      abortIfDirty: true,
    });
  }

  function runPageScripts(root) {
    for (const oldScript of root.querySelectorAll("script")) {
      const newScript = document.createElement("script");
      for (const attribute of oldScript.attributes) {
        newScript.setAttribute(attribute.name, attribute.value);
      }
      newScript.textContent = oldScript.textContent;
      oldScript.replaceWith(newScript);
    }
  }

  function scrollToHash(hash) {
    if (!hash) return;
    const target = document.getElementById(decodeURIComponent(hash.slice(1)));
    if (target) target.scrollIntoView();
  }

  async function navigateTo(href, {
    historyMode = "push",
    force = false,
    abortIfDirty = false,
  } = {}) {
    const target = new URL(href, window.location.href);
    if (target.origin !== window.location.origin || !adminPaths.has(target.pathname)) {
      window.location.assign(target.href);
      return;
    }

    if (!force && target.pathname === window.location.pathname && target.search === window.location.search) {
      if (target.hash !== window.location.hash) {
        if (historyMode === "push") window.history.pushState({}, "", target.href);
        scrollToHash(target.hash);
      }
      return;
    }

    const requestSequence = ++navigationSequence;
    try {
      const response = await fetch(target.pathname + target.search, {
        credentials: "same-origin",
        headers: { "X-Requested-With": "admin-navigation" },
      });
      if (!response.ok) throw new Error("Page request failed with status " + response.status + ".");

      const page = new DOMParser().parseFromString(await response.text(), "text/html");
      const nextShell = page.querySelector("#adminAppShell");
      const nextWidget = page.querySelector(".global-assistant-widget");
      if (!nextShell || !nextWidget) throw new Error("The requested page did not contain the admin shell.");
      if (requestSequence !== navigationSequence) return;
      if (abortIfDirty && hasUnsavedFormChanges()) {
        deferDataRefresh();
        return;
      }

      const isAssistantPage = target.pathname === "/admin/assistant";
      if (isAssistantPage) {
        const nextContent = nextShell.querySelector(".admin-content");
        const nextConsole = nextShell.querySelector("#ai-copilot");
        const nextSettingsMenu = nextShell.querySelector(".admin-topbar .assistant-settings-menu");
        if (!nextContent || !nextConsole || !nextSettingsMenu) {
          throw new Error("The AI Engine page is missing its assistant console.");
        }

        nextConsole.remove();
        nextContent.querySelectorAll("script").forEach((script) => script.remove());
        nextContent.append(assistantConsole);
        nextSettingsMenu.replaceWith(assistantSettingsMenu);
        launcher.hidden = true;
        panel.hidden = true;
        widget.classList.remove("is-open");
      } else {
        const wasAssistantPage = document.body.classList.contains("assistant-page");
        if (assistantConsole.parentElement !== panelContent) panelContent.append(assistantConsole);
        if (assistantSettingsMenu.parentElement !== panelActions) {
          panelActions.insertBefore(assistantSettingsMenu, closeButton);
        }
        launcher.hidden = false;
        if (wasAssistantPage) {
          panel.hidden = true;
          widget.classList.remove("is-open");
        }
      }

      document.dispatchEvent(new Event("admin:page-unmount"));
      currentShell.replaceWith(nextShell);
      currentShell = nextShell;
      pendingDataRefresh = false;
      document.body.className = page.body.className;
      document.title = page.title;

      const pageContext = nextWidget.dataset.assistantPageContext || "assistant";
      widget.dataset.assistantPageContext = pageContext;
      assistantConsole.dataset.assistantPageContext = pageContext;

      if (historyMode === "push") window.history.pushState({}, "", target.href);
      else if (historyMode === "replace") window.history.replaceState({}, "", target.href);

      runPageScripts(currentShell);
      requestAnimationFrame(() => scrollToHash(target.hash));
    } catch (error) {
      if (requestSequence !== navigationSequence) return;
      console.error("Could not navigate within the admin workspace; loading the page normally.", error);
      window.location.assign(target.href);
    }
  }

  window.adminNavigateTo = (href) => navigateTo(href, { historyMode: "replace", force: true });
  window.adminNavigateToIfClean = (href) => navigateTo(href, {
    historyMode: "replace",
    force: true,
    abortIfDirty: true,
  });
  window.refreshAdminPageAfterDataChange = () => {
    if (window.location.pathname === "/admin/content") {
      document.dispatchEvent(new Event("admin:data-changed"));
      return "checking";
    }
    if (hasUnsavedFormChanges()) {
      deferDataRefresh();
      return "deferred";
    }
    void navigateTo(window.location.href, {
      historyMode: "replace",
      force: true,
      abortIfDirty: true,
    });
    return "refreshing";
  };

  document.addEventListener("click", (event) => {
    if (
      event.defaultPrevented || event.button !== 0
      || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey
    ) return;
    if (!(event.target instanceof Element)) return;

    const link = event.target.closest("a[href]");
    if (!link || (link.target && link.target !== "_self") || link.hasAttribute("download")) return;
    const target = new URL(link.href, window.location.href);
    if (target.origin !== window.location.origin || !adminPaths.has(target.pathname)) return;

    event.preventDefault();
    void navigateTo(target.href);
  });

  window.addEventListener("popstate", () => {
    void navigateTo(window.location.href, { historyMode: "none", force: true });
  });
  document.addEventListener("input", refreshPendingDataWhenClean);
  document.addEventListener("change", refreshPendingDataWhenClean);
})();
