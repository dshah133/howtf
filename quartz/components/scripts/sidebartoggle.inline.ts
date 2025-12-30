const sidebarPref = localStorage.getItem("sidebar") ?? "visible"
document.documentElement.setAttribute("sidebar-state", sidebarPref)

document.addEventListener("nav", () => {
  const toggleSidebar = () => {
    const newState =
      document.documentElement.getAttribute("sidebar-state") === "visible" ? "hidden" : "visible"
    document.documentElement.setAttribute("sidebar-state", newState)
    localStorage.setItem("sidebar", newState)
  }

  const toggleButtons = document.getElementsByClassName("sidebar-toggle")
  for (let i = 0; i < toggleButtons.length; i++) {
    const toggleButton = toggleButtons[i]
    toggleButton.addEventListener("click", toggleSidebar)
    ;(window as any).addCleanup(() => toggleButton.removeEventListener("click", toggleSidebar))
  }
})
