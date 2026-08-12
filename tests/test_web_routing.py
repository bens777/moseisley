"""Guards on the authenticated web shell's routing.

The registration flow is load-bearing and has no browser test in this repo, so
these read the actual sources: register → cantina overlay → /welcome (Crew
Genesis) → home, and the Command Center staying at /command for every deep
link that already exists in the wild.
"""

from __future__ import annotations

from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "apps" / "web"

HOME = "/manager"


def read(rel: str) -> str:
    return (WEB / rel).read_text(encoding="utf-8")


def test_home_path_is_the_unified_chat():
    assert f'HOME_PATH = "{HOME}"' in read("lib/routes.ts")
    home_page = read("app/manager/page.tsx")
    # both threads are tabs on home, each mounting its own existing component
    assert 'variant="page"' in home_page
    assert "<ManagerConversation" in home_page
    assert "<OrchestratorThread" in home_page
    # the tab bar is data-driven, so a third agent is a row and not a rewrite
    assert "const TABS: ChatTab[]" in home_page
    assert "TABS.map((t)" in home_page


def test_chat_route_redirects_to_home_instead_of_duplicating_it():
    chat = read("app/chat/page.tsx")
    assert "redirect(HOME_PATH)" in chat
    # the threads moved out whole; no second copy left behind here
    assert "OrchestratorThread" not in chat
    assert "ManagerConversation" not in chat
    assert "OrchestratorThread" in read("components/orchestrator.tsx")


def test_registration_redirect_chain():
    """register → /welcome (Genesis) → home, unchanged in shape."""
    auth = read("components/auth-form.tsx")
    # registration still posts to /auth/register and still routes to Genesis
    assert "/api/auth/register" in auth
    assert 'router.replace(mode === "register" ? "/welcome" : HOME_PATH);' in auth
    # the cantina "preparing" overlay still covers the register → welcome wait
    assert "setPreparing(true)" in auth

    genesis = read("app/welcome/page.tsx")
    assert "/crew/genesis/apply" in genesis
    # both exits from Genesis (create + skip) land on the Manager conversation
    assert genesis.count("router.replace(HOME_PATH)") == 2
    assert 'router.replace("/command")' not in genesis


def test_command_center_still_lives_at_its_own_route():
    """Deep links to the Command Center must keep resolving to its content."""
    page = read("app/command/page.tsx")
    for marker in ("Command center initialization", "Mission progress",
                   "Crew status", "Radar", "Today's operations"):
        assert marker in page, marker


def test_sidebar_promotes_the_manager_and_command_center():
    shell = read("components/shell.tsx")
    assert 'HOME_NAV: NavItem = { href: HOME_PATH, label: "Manager"' in shell
    primary = shell.split("const PRIMARY_NAV")[1].split("];")[0]
    advanced = shell.split("const ADVANCED_NAV")[1].split("];")[0]
    for href in ('"/command"', '"/agents"', '"/bar"'):
        assert href in primary, href
        assert href not in advanced, href
    # /chat is gone from the nav: it redirects to the top "Manager" entry, so a
    # separate row would be the same destination twice
    assert '"/chat"' not in primary
    assert '"/chat"' not in advanced


def test_manager_bar_is_hidden_where_the_page_has_its_own_chat():
    """No page shows two chat surfaces at once: home and /chat own theirs."""
    shell = read("components/shell.tsx")
    assert "const isHome = pathname === HOME_PATH;" in shell
    assert 'const hasOwnChat = isHome || pathname === "/chat";' in shell
    assert "{!hasOwnChat && <ManagerDock />}" in shell
    # only home takes over the viewport; /chat keeps the normal padded frame
    assert "isHome\n          ? \"flex min-h-0 min-w-0 flex-1 flex-col\"" in shell
