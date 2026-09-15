import os
import time
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

PANEL_URL = "https://shulker.in/"
LOGIN_URL = "https://shulker.in/panel/auth/login/"
DASHBOARD_URL = "https://shulker.in/panel/dashboard/"


def log(msg):
    print(f"[INFO] {msg}")


def warn(msg):
    print(f"[WARN] {msg}")


def err(msg):
    print(f"[ERROR] {msg}")


def login(page, username, password):
    """
    登录面板。以下是常见 Pterodactyl 类系面板的典型选择器，
    如果匹配不上，需要根据实际截图调整。
    """
    log("正在打开登录页...")
    page.goto(LOGIN_URL, timeout=60000)
    page.wait_for_timeout(2000)

    username_candidates = [
        lambda: page.get_by_placeholder("Username or Email"),
        lambda: page.get_by_placeholder("Email"),
        lambda: page.get_by_placeholder("Username"),
        lambda: page.locator('input[name="username"]'),
        lambda: page.locator('input[name="email"]'),
        lambda: page.locator('input[type="email"]'),
    ]
    password_candidates = [
        lambda: page.get_by_placeholder("Password"),
        lambda: page.locator('input[name="password"]'),
        lambda: page.locator('input[type="password"]'),
    ]

    username_input = None
    for cand in username_candidates:
        loc = cand()
        if loc.count() > 0:
            username_input = loc.first
            break
    if username_input is None:
        raise RuntimeError("未找到用户名/邮箱输入框，请提供登录页截图以确定实际选择器")

    password_input = None
    for cand in password_candidates:
        loc = cand()
        if loc.count() > 0:
            password_input = loc.first
            break
    if password_input is None:
        raise RuntimeError("未找到密码输入框，请提供登录页截图以确定实际选择器")

    username_input.fill(username)
    password_input.fill(password)
    log("已填入用户名和密码")

    login_btn_texts = ["Sign In", "Login", "Log in", "登录"]
    clicked = False
    for text in login_btn_texts:
        btn = page.locator(f'button:has-text("{text}")')
        if btn.count() > 0:
            btn.first.click()
            clicked = True
            log(f"点击了登录按钮 (文字: {text})")
            break
    if not clicked:
        submit_btn = page.locator('button[type="submit"]')
        if submit_btn.count() > 0:
            submit_btn.first.click()
            clicked = True
            log("点击了 type=submit 的按钮")
    if not clicked:
        raise RuntimeError("未找到登录按钮，请提供登录页截图以确定实际按钮文字")

    page.wait_for_timeout(3000)

    # 和 host-ship 面板类似，未登录/已登录可能停留在相似 URL，
    # 用"密码框是否还在"判断是否登录成功，比对比 URL 更可靠。
    if page.get_by_placeholder("Password").count() > 0 or page.locator('input[type="password"]').count() > 0:
        screenshot_path = "login_failed.png"
        try:
            page.screenshot(path=screenshot_path, full_page=True)
            err(f"已保存失败截图: {screenshot_path}（会作为 Actions Artifact 上传）")
        except Exception as e:
            warn(f"截图保存失败: {e}")
        raise RuntimeError("登录后仍能看到密码输入框，可能账号密码错误，或页面出现验证码/人机校验")

    log(f"登录成功，当前 URL: {page.url}")


def find_server_links(page):
    """
    收集服务器详情页链接。优先按 href 是否包含 "/server/" 判断，
    这是实测中真正的游戏服务器详情页特征；
    "Manage"/"Console" 这类文字按钮可能会误匹配到订阅管理等无关页面，仅作兜底。
    """
    log(f"正在打开 Dashboard: {DASHBOARD_URL}")
    page.goto(DASHBOARD_URL, timeout=60000)
    page.wait_for_timeout(3000)

    candidate_selectors = [
        'a[href*="/server/"]',
        'a[href*="/servers/"]',
    ]
    for sel in candidate_selectors:
        links = page.locator(sel)
        count = links.count()
        if count > 0:
            hrefs = []
            for i in range(count):
                href = links.nth(i).get_attribute("href")
                if href and href not in hrefs:
                    hrefs.append(href)
            log(f"使用选择器 '{sel}' 找到 {len(hrefs)} 个服务器")
            return hrefs

    candidate_texts = ["Manage Server", "Manage", "Console", "View Server"]
    for text in candidate_texts:
        links = page.locator(f'a:has-text("{text}")')
        count = links.count()
        if count > 0:
            hrefs = []
            for i in range(count):
                href = links.nth(i).get_attribute("href")
                if href and href not in hrefs:
                    hrefs.append(href)
            log(f"通过 '{text}' 按钮找到 {len(hrefs)} 个服务器（兜底匹配，可能包含无关链接）")
            return hrefs

    warn("未匹配到任何服务器链接，请提供 Dashboard 页面截图以确定实际结构")
    return []


def ensure_server_running(page, url):
    """
    进入单个 DevSpace 详情页，检测是否离线，离线则点击 Start container。
    根据实际截图确认：
    - 容器离线时页面会显示明确文字 "Container offline"，并有按钮 "Start container"
    - 这个判断依据比读取模糊的状态文字（如 Running/Offline 泛用词）更准确，
      因为后者容易在 WebSocket 尚未连接完成时读到过渡态/默认值
    """
    full_url = url if url.startswith("http") else f"{PANEL_URL.rstrip('/')}{url}"
    log(f"打开服务器详情页: {full_url}")
    page.goto(full_url, timeout=60000)
    # 延长等待时间，给 WebSocket 连接和实时状态刷新留出时间
    page.wait_for_timeout(6000)

    offline_indicator = page.locator('text="Container offline"')
    is_offline = offline_indicator.count() > 0 and offline_indicator.first.is_visible(timeout=2000)

    if not is_offline:
        log("未检测到 'Container offline' 提示，判断为已在运行，无需操作")
        return "already_running"

    log("检测到 'Container offline'，容器已离线，尝试点击 Start container...")

    start_btn = page.locator('button:has-text("Start container")')
    if start_btn.count() == 0:
        start_btn = page.locator('button:has-text("Start")')

    if start_btn.count() == 0 or not start_btn.first.is_visible(timeout=3000):
        warn(f"检测到离线，但在 {full_url} 未找到 Start container 按钮，请提供该页面截图确认")
        return "not_found"

    start_btn.first.scroll_into_view_if_needed()
    start_btn.first.click()
    log("已点击 Start container，开机指令已发送")
    page.wait_for_timeout(4000)

    try:
        page.screenshot(path="start_result.png", full_page=True)
        log("已保存开机点击后的截图: start_result.png（会作为 Actions Artifact 上传）")
    except Exception as e:
        warn(f"截图保存失败: {e}")

    return "started"


def run(playwright):
    username = os.environ.get("SHULKER_USERNAME", "").strip()
    password = os.environ.get("SHULKER_PASSWORD", "").strip()

    if not username or not password:
        err("未设置 SHULKER_USERNAME 或 SHULKER_PASSWORD 环境变量")
        return

    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    page = context.new_page()

    try:
        login(page, username, password)

        hrefs = find_server_links(page)
        if not hrefs:
            err("未找到任何服务器，流程终止")
            return

        results = {"started": 0, "already_running": 0, "not_found": 0}
        for idx, href in enumerate(hrefs):
            log(f"--- 处理第 {idx + 1}/{len(hrefs)} 个服务器 ---")
            try:
                result = ensure_server_running(page, href)
                results[result] = results.get(result, 0) + 1
            except PlaywrightTimeout:
                warn(f"处理 {href} 超时，跳过")
            except Exception as e:
                warn(f"处理 {href} 时出错: {e}")

        log(f"全部处理完成: {results}")

    except Exception as e:
        err(f"执行过程中发生错误: {e}")
        try:
            page.screenshot(path="error.png", full_page=True)
            err("已保存错误截图: error.png（会作为 Actions Artifact 上传）")
        except Exception as shot_err:
            warn(f"错误截图保存失败: {shot_err}")
    finally:
        browser.close()


with sync_playwright() as playwright:
    run(playwright)
