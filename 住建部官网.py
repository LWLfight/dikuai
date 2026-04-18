#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import json
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.common.exceptions import TimeoutException, WebDriverException
import time
import re
import random

# 全局变量,用于存储搜索结果信息
TOTAL_RESULTS = 0  # 总结果数
PER_PAGE = 10      # 每页显示数量


def open_website():
    """
    使用Selenium打开住建部官网
    """
    driver = None
    # 目标网址 - 修改为住建部官网
    target_url = "https://www.mohurd.gov.cn/"
    
    # 配置浏览器选项
    chrome_options = uc.ChromeOptions()
    
    # 性能优化配置
    chrome_options.add_argument('--no-sandbox')              # 提升启动稳定性与速度
    chrome_options.add_argument('--disable-dev-shm-usage')   # 解决资源限制
    chrome_options.add_argument('--disable-extensions')      # 禁用扩展,减少初始化开销
    chrome_options.add_argument('--disable-plugins')         # 禁用插件,减少初始化开销
    chrome_options.add_argument('--disable-images')          # 禁用图片加载,大幅提升页面加载速度
    
    # GPU加速配置(保留GPU以提升渲染性能)
    # 注意: 移除了 --disable-gpu,启用GPU加速
    
    try:
        print("正在初始化浏览器...")
        start_time = time.time()
        
        # 创建浏览器实例 - 使用undetected_chromedriver自动管理驱动版本
        # 指定 Chrome 主版本号以避免版本检测问题
        driver = uc.Chrome(options=chrome_options, version_main=147)
        
        init_time = time.time() - start_time
        print(f"浏览器初始化完成,耗时: {init_time:.2f}秒")
        
        # 设置隐式等待(减少到5秒,避免不必要的阻塞)
        driver.implicitly_wait(5)
        
        print("浏览器初始化成功!")
        print(f"正在访问: {target_url}")
        
        # 打开目标网址
        page_start = time.time()
        driver.get(target_url)
        
        # 显示等待：等待页面标题加载完成(最多等待10秒,减少超时时间)
        try:
            WebDriverWait(driver, 10).until(
                lambda d: d.title is not None and len(d.title) > 0
            )
            page_load_time = time.time() - page_start
            print(f"页面标题: {driver.title}")
            print(f"页面加载耗时: {page_load_time:.2f}秒")
        except TimeoutException:
            print("警告：页面标题加载超时，但页面可能已打开")
        
        # 获取当前URL（可能会有重定向）
        current_url = driver.current_url
        print(f"当前实际URL: {current_url}")
        
        # 打印页面源代码长度（验证页面是否加载成功）
        page_source_len = len(driver.page_source)
        print(f"页面源代码长度: {page_source_len} 字符")
        
        if page_source_len > 1000:
            print("页面加载成功！")
        else:
            print("警告：页面内容较少，可能未完全加载")
        
    except WebDriverException as e:
        print(f"浏览器驱动错误: {e}")
        print("请确保：")
        print("1. chromedriver已正确安装")
        print("2. chromedriver版本与Chrome浏览器版本匹配")
        print("3. chromedriver已添加到系统PATH或已指定路径")
        return None
    except Exception as e:
        print(f"发生错误: {e}")
        return None
    
    return driver

def search(driver, keyword="名城", output_filename="住建部爬取结果.txt"):
    """
    在搜索框中输入关键词进行搜索
    
    参数:
        driver: Selenium WebDriver实例
        keyword: 搜索关键词,默认为"名城"
        output_filename: 输出文件名,默认为"住建部爬取结果.txt"
    """
    global TOTAL_RESULTS, PER_PAGE
    
    if not driver:
        print("错误: 浏览器驱动未初始化")
        return []

    try:
        # 确保在主页窗口
        main_window = driver.current_window_handle
        
        # 查找搜索框 (id='searchInput') - 修改为新的选择器
        print(f"\n{'='*60}")
        print(f"正在搜索关键词: {keyword}")
        print(f"{'='*60}")
        
        # 先保存主页HTML用于调试
        try:
            with open("homepage_debug.html", "w", encoding="utf-8") as f:
                f.write(driver.page_source)
            print("已保存主页HTML到 homepage_debug.html")
        except:
            pass
        
        print("正在查找搜索框...")
        search_box = WebDriverWait(driver, 10).until(
            lambda d: d.find_element(By.ID, 'searchInput')
        )
        print("找到搜索框!")
        
        # 先清空,然后输入关键词 - 使用select_all + delete的方式
        from selenium.webdriver.common.keys import Keys
        search_box.send_keys(Keys.CONTROL + 'a')  # 全选
        search_box.send_keys(Keys.DELETE)  # 删除
        time.sleep(0.5)  # 等待清空完成
        
        # 逐个字符输入关键词,确保触发input事件
        for char in keyword:
            search_box.send_keys(char)
            time.sleep(0.1)
        
        print(f"已输入关键词: {keyword}")
        time.sleep(1.5)  # 增加等待时间,确保输入完全生效
        
        # 尝试多种搜索方式
        search_success = False
        
        # 方式1: 在输入框中按回车键(优先使用,更稳定)
        try:
            print("尝试方式1: 在输入框中按回车键...")
            from selenium.webdriver.common.keys import Keys
            search_box.send_keys(Keys.RETURN)
            time.sleep(6)  # 增加等待时间
            
            # 检查URL是否变化为搜索结果页面
            if "search" in driver.current_url.lower() or "api-gateway" in driver.current_url:
                print("✓ 回车键搜索成功!")
                search_success = True
            else:
                print(f"  回车键未触发搜索,当前URL: {driver.current_url}")
                # 如果还在主页,说明回车键无效,需要重试
                if "mohurd.gov.cn/" == driver.current_url or driver.current_url.endswith("/"):
                    print("  仍在主页,准备尝试其他方式...")
        except Exception as e:
            print(f"  回车键搜索失败: {e}")
        
        # 方式2: 如果回车键失败,尝试再次输入后按回车(带重试机制)
        if not search_success:
            try:
                print("\n尝试方式2: 重新输入关键词并按回车...")
                from selenium.webdriver.common.keys import Keys
                
                max_retries = 3  # 最多尝试3次
                retry_count = 0
                
                while retry_count < max_retries and not search_success:
                    if retry_count > 0:
                        # 根据重试次数决定等待时间
                        if retry_count == 1:
                            wait_time = random.uniform(10, 15)
                            print(f"\n  第1次尝试失败,等待 {wait_time:.1f} 秒后重试...")
                        elif retry_count == 2:
                            wait_time = random.uniform(25, 30)
                            print(f"\n  第2次尝试失败,等待 {wait_time:.1f} 秒后重试...")
                        
                        time.sleep(wait_time)
                        
                        # 重新定位搜索框(页面可能已刷新)
                        try:
                            search_box = WebDriverWait(driver, 10).until(
                                lambda d: d.find_element(By.ID, 'searchInput')
                            )
                            print(f"  第{retry_count + 1}次重试: 重新找到搜索框")
                        except Exception as box_err:
                            print(f"  第{retry_count + 1}次重试: 无法找到搜索框 - {box_err}")
                            break
                    
                    # 清空输入框
                    search_box.send_keys(Keys.CONTROL + 'a')
                    search_box.send_keys(Keys.DELETE)
                    time.sleep(0.8)
                    
                    # 重新逐个字符输入
                    for char in keyword:
                        search_box.send_keys(char)
                        time.sleep(0.08)
                    
                    time.sleep(1)  # 等待输入完成
                    
                    # 按回车
                    search_box.send_keys(Keys.RETURN)
                    time.sleep(7)
                    
                    # 检查结果
                    if "search" in driver.current_url.lower() or "api-gateway" in driver.current_url:
                        print(f"✓ 第{retry_count + 1}次尝试成功!")
                        search_success = True
                    else:
                        print(f"  第{retry_count + 1}次尝试未成功,当前URL: {driver.current_url[:80]}")
                        retry_count += 1
                
                if not search_success:
                    print(f"  方式2经过{max_retries}次尝试后仍未成功")
                    
            except Exception as e:
                print(f"  方式2执行出错: {e}")
                import traceback
                traceback.print_exc()
        
        # 方式3: 如果都失败,尝试点击搜索按钮
        if not search_success:
            try:
                print("\n尝试方式3: 查找并点击搜索按钮...")
                
                # 尝试多种可能的选择器来定位搜索按钮
                submit_button = None
                button_selectors = [
                    (By.ID, 'toSearchBtn'),  # ✅ 优先使用ID(最可靠)
                    (By.CSS_SELECTOR, 'a.search-btn'),  # ✅ 使用class
                    (By.XPATH, '//a[@id="toSearchBtn"]'),  # ✅ XPath通过ID
                    (By.XPATH, '//a[@title="搜索按钮"]'),  # ✅ 通过title属性
                    (By.CSS_SELECTOR, '.search-btn'),  # 备用class选择器
                ]
                
                for by, selector in button_selectors:
                    try:
                        submit_button = WebDriverWait(driver, 3).until(
                            lambda d, s=selector: d.find_element(by, s)
                        )
                        print(f"找到搜索按钮! (使用选择器: {selector})")
                        break
                    except:
                        continue
                
                if submit_button:
                    # 先确保输入框中有内容
                    current_value = search_box.get_attribute('value')
                    if not current_value or current_value.strip() != keyword:
                        print(f"  输入框内容为空或不正确,重新输入...")
                        search_box.send_keys(Keys.CONTROL + 'a')
                        search_box.send_keys(Keys.DELETE)
                        time.sleep(0.3)
                        for char in keyword:
                            search_box.send_keys(char)
                            time.sleep(0.05)
                        time.sleep(0.5)
                    
                    # 使用JavaScript点击搜索按钮(更可靠)
                    driver.execute_script("arguments[0].click();", submit_button)
                    print("已点击搜索按钮,页面正在跳转...")
                    time.sleep(8)
                    
                    # 检查URL是否变化为搜索结果页面
                    if "search" in driver.current_url.lower() or "api-gateway" in driver.current_url:
                        print("✓ 按钮点击搜索成功!")
                        search_success = True
                    else:
                        print(f"  按钮点击后URL: {driver.current_url}")
                        print(f"  警告: 可能点击了错误的按钮或搜索未触发")
                else:
                    print("未找到搜索按钮")
            except Exception as e:
                print(f"  按钮点击搜索失败: {e}")
                import traceback
                traceback.print_exc()
        
        # 方式4: 最后的尝试 - 直接构造搜索URL(使用从成功案例中获取的正确格式)
        if not search_success:
            try:
                print("\n尝试方式4: 直接构造搜索URL...")
                from urllib.parse import quote
                
                # 使用从成功案例中分析出的正确URL格式
                search_urls = [
                    # 正确的API格式(从成功案例中提取)
                    f"https://www.mohurd.gov.cn/api-gateway/jpaas-jsearch-web-server/search?serviceId=e2f3058e2a3b4f8abc93eb76e739e3e7&websiteid=&cateid=6ca0f12c0f0642ab8b1dc17028e12ea1&q={quote(keyword)}",
                    # 备用格式
                    f"https://www.mohurd.gov.cn/api-gateway/homepage/searchInfos?pageNum=1&pageSize=10&keywords={quote(keyword)}",
                ]
                
                for search_url in search_urls:
                    try:
                        print(f"  尝试URL: {search_url[:100]}...")
                        driver.get(search_url)
                        time.sleep(6)  # 增加等待时间
                        
                        # 检查是否是搜索结果页
                        if "search" in driver.current_url.lower() or "api-gateway" in driver.current_url:
                            # 验证是否有搜索结果或搜索界面
                            try:
                                # 检查是否有搜索结果链接
                                result_links = driver.find_elements(By.CSS_SELECTOR, 'a.textTitle')
                                if result_links:
                                    print(f"✓ 直接访问搜索URL成功! 找到 {len(result_links)} 个结果")
                                    search_success = True
                                    break
                                else:
                                    # 即使没有结果,只要页面是搜索页也算成功
                                    print(f"  搜索页面无结果(可能该关键词确实无数据)")
                                    # 检查页面是否有搜索相关的元素
                                    if len(driver.page_source) > 5000:  # 页面有内容
                                        print(f"  ✓ 搜索页面加载成功,但该关键词可能无匹配结果")
                                        search_success = True
                                        break
                                    else:
                                        print(f"  页面内容异常,尝试下一个URL...")
                            except Exception as check_err:
                                print(f"  检查结果时出错: {check_err},尝试下一个URL...")
                        else:
                            print(f"  URL未跳转到搜索页,当前: {driver.current_url[:60]}")
                    except Exception as url_err:
                        print(f"  URL访问失败: {url_err}")
                        continue
                        
            except Exception as e:
                print(f"  直接URL访问失败: {e}")
        
        # 方式5: 如果所有方式都失败,记录但继续处理下一个关键词
        if not search_success:
            print(f"\n⚠️ 警告: 关键词 '{keyword}' 的所有搜索方式均失败")
            print(f"   可能原因:")
            print(f"   1. 该关键词在网站上确实无相关结果")
            print(f"   2. 网站搜索服务暂时不可用")
            print(f"   3. 网络请求被拦截或超时")
            print(f"   将继续处理下一个关键词...")
            return []  # 返回空列表,但不抛出异常
        
        # 等待新页面加载完成
        time.sleep(8)
        
        # 检查是否有新窗口打开
        all_windows = driver.window_handles
        print(f"当前窗口数量: {len(all_windows)}")
        
        search_window = None
        if len(all_windows) > 1:
            # 切换到最新的搜索结果窗口
            search_window = all_windows[-1]
            print(f"检测到 {len(all_windows)} 个窗口,切换到搜索结果窗口...")
            driver.switch_to.window(search_window)
            print(f"当前窗口URL: {driver.current_url}")
        else:
            print("只有一个窗口,在当前窗口中加载了搜索结果")
            search_window = main_window
        
        # 打印当前URL,确认是否跳转到搜索结果页
        print(f"当前页面URL: {driver.current_url}")
        print(f"当前页面标题: {driver.title}")
        
        # 保存搜索结果页面的HTML以便分析
        with open('search_result_page.html', 'w', encoding='utf-8') as f:
            f.write(driver.page_source)
        print("已保存搜索结果页面到 search_result_page.html")
        
        # 获取搜索结果数量 - 根据实际页面结构调整
        try:
            # 查找总页数信息: <li class="totalPage">共&nbsp;36&nbsp;页</li>
            # 增加等待时间,确保页面完全加载
            time.sleep(2)
            
            total_page_elem = WebDriverWait(driver, 10).until(
                lambda d: d.find_element(By.CSS_SELECTOR, 'li.totalPage:last-child')
            )
            
            # 提取总页数文本并解析数字
            total_page_text = total_page_elem.text  # "共 36 页"
            page_match = re.search(r'(\d+)', total_page_text)
            
            if page_match:
                total_pages = int(page_match.group(1))
                PER_PAGE = 10  # 每页显示10条(默认值)
                TOTAL_RESULTS = total_pages * PER_PAGE
                
                print(f"\n{'='*50}")
                print(f"搜索结果信息:")
                print(f"  总页数: {total_pages} 页")
                print(f"  每页显示: {PER_PAGE} 条(估算)")
                print(f"  预计总结果数: 约 {TOTAL_RESULTS} 条")
                print(f"{'='*50}")
            else:
                print("\n警告: 无法解析总页数")
                TOTAL_RESULTS = 9999  # 设置一个大数,依靠翻页按钮判断结束
                PER_PAGE = 10
            
        except TimeoutException:
            print("\n警告: 未找到总页数信息 (超时10秒)")
            TOTAL_RESULTS = 9999  # 设置一个大数,依靠翻页按钮判断结束
            PER_PAGE = 10
        except Exception as e:
            print(f"获取搜索结果数量时出错: {e}")
            import traceback
            traceback.print_exc()
            TOTAL_RESULTS = 9999
            PER_PAGE = 10
        
        # 获取搜索结果列表中的标题 - 让extract_search_result_titles根据100条限制自动控制
        results = extract_search_result_titles(driver, keyword=keyword, output_filename=output_filename)
        
        print(f"\n关键词 '{keyword}' 搜索完成! 共提取 {len(results)} 条数据")
        
        # 关闭搜索结果窗口,返回主页
        try:
            current_windows = driver.window_handles
            if len(current_windows) > 1 and search_window in current_windows:
                print(f"\n正在关闭搜索结果窗口...")
                driver.close()  # 关闭当前搜索结果窗口
                print("已关闭搜索结果窗口")
                
                # 切换回主窗口
                if main_window in driver.window_handles:
                    driver.switch_to.window(main_window)
                    print(f"已切换回主窗口: {driver.current_url}")
                    
                    # 重新加载主页,确保搜索框可用
                    print("重新加载主页...")
                    try:
                        driver.get("https://www.mohurd.gov.cn/")
                        time.sleep(3)
                        
                        # 验证主页是否加载成功
                        WebDriverWait(driver, 10).until(
                            lambda d: d.find_element(By.ID, 'searchInput')
                        )
                        print("主页重新加载完成,搜索框可用")
                    except Exception as e:
                        print(f"重新加载主页失败: {e}")
                        raise
                else:
                    print("警告: 主窗口已不存在,重新打开主页")
                    driver.get("https://www.mohurd.gov.cn/")
                    time.sleep(3)
            else:
                # 如果只有一个窗口,直接重新加载主页
                print("\n只有一个窗口,重新加载主页准备下一次搜索...")
                try:
                    driver.get("https://www.mohurd.gov.cn/")
                    time.sleep(3)
                    
                    # 验证主页是否加载成功
                    WebDriverWait(driver, 10).until(
                        lambda d: d.find_element(By.ID, 'searchInput')
                    )
                    print("主页重新加载完成,搜索框可用")
                except Exception as e:
                    print(f"重新加载主页失败: {e}")
                    raise
        except Exception as e:
            print(f"关闭窗口时出错: {e}")
            # 尝试重新打开主页
            try:
                print("尝试重新打开主页...")
                driver.get("https://www.mohurd.gov.cn/")
                time.sleep(3)
                
                # 验证主页是否加载成功
                WebDriverWait(driver, 10).until(
                    lambda d: d.find_element(By.ID, 'searchInput')
                )
                print("主页重新加载完成")
            except Exception as recovery_err:
                print(f"恢复主页失败: {recovery_err}")
                raise  # 抛出异常,让上层处理
        
        return results
        
    except Exception as e:
        print(f"搜索过程中发生错误: {e}")
        import traceback
        traceback.print_exc()
        
        # 发生错误时也尝试恢复
        try:
            print("尝试恢复到主页...")
            driver.get("https://www.mohurd.gov.cn/")
            time.sleep(3)
            
            # 验证主页是否加载成功
            WebDriverWait(driver, 10).until(
                lambda d: d.find_element(By.ID, 'searchInput')
            )
            print("已恢复到主页")
        except Exception as recovery_err:
            print(f"恢复到主页失败: {recovery_err}")
        
        return []


def check_anti_crawl(driver, consecutive_failures=0):
    """
    检测是否触发反爬机制,并根据失败次数执行相应的等待策略
    
    参数:
        driver: Selenium WebDriver实例
        consecutive_failures: 连续失败次数
    
    返回:
        bool: True表示检测到反爬,需要等待; False表示正常
    """
    # 检测常见的反爬特征
    anti_crawl_indicators = [
        # 验证码页面
        lambda: "captcha" in driver.current_url.lower(),
        lambda: "verify" in driver.current_url.lower(),
        # 访问受限页面
        lambda: "blocked" in driver.current_url.lower(),
        lambda: "forbidden" in driver.current_url.lower(),
        # 页面内容异常
        lambda: len(driver.page_source) < 1000,
        # 检查是否有验证码元素
        lambda: len(driver.find_elements(By.XPATH, "//div[contains(text(), '验证')]")) > 0,
        lambda: len(driver.find_elements(By.XPATH, "//div[contains(text(), 'captcha')]")) > 0,
    ]
    
    is_blocked = any(indicator() for indicator in anti_crawl_indicators)
    
    if is_blocked:
        # 根据连续失败次数确定等待时间
        if consecutive_failures == 0:
            wait_time = random.uniform(10, 15)
            level = "第1级"
        elif consecutive_failures == 1:
            wait_time = random.uniform(20, 25)
            level = "第2级"
        elif consecutive_failures == 2:
            wait_time = random.uniform(50, 60)
            level = "第3级"
        else:
            wait_time = random.uniform(100, 120)
            level = "第4级(最高)"
        
        print(f"\n⚠️ 检测到反爬机制触发! ({level})")
        print(f"   连续失败次数: {consecutive_failures + 1}")
        print(f"   等待时间: {wait_time:.1f} 秒")
        print(f"   正在等待...")
        
        time.sleep(wait_time)
        return True
    
    return False


def handle_anti_crawl_recovery(driver, consecutive_failures):
    """
    反爬恢复策略:刷新页面或重新导航
    
    参数:
        driver: Selenium WebDriver实例
        consecutive_failures: 连续失败次数
    
    返回:
        bool: True表示恢复成功, False表示恢复失败
    """
    try:
        print(f"   尝试恢复访问...")
        
        # 记录当前URL
        current_url = driver.current_url
        
        # 刷新页面
        driver.refresh()
        time.sleep(5)
        
        # 检查是否恢复正常
        if len(driver.page_source) > 1000:
            print(f"   ✓ 页面刷新成功,已恢复正常访问")
            return True
        else:
            # 如果刷新无效,尝试重新导航
            print(f"   刷新无效,尝试重新导航...")
            driver.get(current_url)
            time.sleep(5)
            
            if len(driver.page_source) > 1000:
                print(f"   ✓ 重新导航成功,已恢复正常访问")
                return True
            else:
                print(f"   ✗ 恢复失败")
                return False
                
    except Exception as e:
        print(f"   ✗ 恢复过程出错: {e}")
        return False


def extract_search_result_titles(driver, keyword="名城", output_filename="住建部爬取结果.txt"):
    """
    提取搜索结果列表中的标题信息,并自动翻页直到获取所有结果
    
    参数:
        driver: Selenium WebDriver实例
        keyword: 当前搜索的关键词
        output_filename: 输出文件名
    """
    global TOTAL_RESULTS, PER_PAGE
    
    MAX_RESULTS_PER_KEYWORD = 100  # 每个关键词最多爬取100条数据
    all_results = []  # 存储所有结果
    seen_urls = set()  # 用于去重
    page_num = 1
    consecutive_failures = 0  # 连续失败次数,用于反爬策略
    
    # 根据总结果数计算理论最大页数
    if TOTAL_RESULTS > 0:
        max_pages = TOTAL_RESULTS // PER_PAGE + (1 if TOTAL_RESULTS % PER_PAGE > 0 else 0)
    else:
        max_pages = 999  # 设置一个大数,依靠100条限制和翻页按钮判断结束
    
    # 如果文件不存在,初始化文件头
    import os
    if not os.path.exists(output_filename):
        try:
            with open(output_filename, 'w', encoding='utf-8') as f:
                f.write(f"国家文物局网站搜索结果\n")
                f.write(f"开始时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"{'='*100}\n\n")
            print(f"\n已创建输出文件: {output_filename}")
        except Exception as e:
            print(f"创建输出文件失败: {e}")
            return []
    
    try:
        print(f"\n开始提取关键词 '{keyword}' 的搜索结果...")
        print(f"{'='*60}")
        print(f"总结果数: {TOTAL_RESULTS} 条")
        print(f"每页显示: {PER_PAGE} 条")
        print(f"本关键词最多爬取: {MAX_RESULTS_PER_KEYWORD} 条")
        
        # 计算实际需要的页数(基于100条限制)
        max_pages_by_limit = (MAX_RESULTS_PER_KEYWORD + PER_PAGE - 1) // PER_PAGE  # 10页
        actual_max_pages = min(max_pages, max_pages_by_limit)
        
        if TOTAL_RESULTS > MAX_RESULTS_PER_KEYWORD:
            print(f"计划爬取页数: {max_pages_by_limit} 页 (达到100条限制)")
        else:
            print(f"计划爬取页数: 最多 {max_pages} 页 (根据实际结果数)")
        
        print(f"{'='*60}")
        
        while page_num <= actual_max_pages:
            # 检查是否已达到最大爬取数量
            if len(all_results) >= MAX_RESULTS_PER_KEYWORD:
                print(f"\n已达到本关键词最大爬取数量限制 ({MAX_RESULTS_PER_KEYWORD} 条),停止爬取")
                break
            
            print(f"\n正在处理第 {page_num} 页...")
            
            # 检测反爬机制
            if check_anti_crawl(driver, consecutive_failures):
                # 尝试恢复
                if not handle_anti_crawl_recovery(driver, consecutive_failures):
                    print(f"   恢复失败,跳过当前页")
                    consecutive_failures += 1
                    page_num += 1
                    continue
                else:
                    # 恢复成功,重置失败计数
                    consecutive_failures = 0
            
            # 查找当前页面的所有搜索结果链接 - 根据实际页面结构
            try:
                # 使用正确的选择器: a.textTitle (搜索结果标题链接)
                result_links = WebDriverWait(driver, 10).until(
                    lambda d: d.find_elements(By.CSS_SELECTOR, 'a.textTitle')
                )
                # 成功获取,重置失败计数
                consecutive_failures = 0
            except TimeoutException:
                print(f"警告: 第 {page_num} 页未找到搜索结果链接")
                consecutive_failures += 1
                
                # 如果连续失败多次,可能是触发了反爬
                if consecutive_failures >= 2:
                    print(f"   连续{consecutive_failures}次失败,可能触发反爬机制")
                    if check_anti_crawl(driver, consecutive_failures - 1):
                        if handle_anti_crawl_recovery(driver, consecutive_failures):
                            consecutive_failures = 0
                            continue
                
                break
            
            if not result_links:
                print(f"第 {page_num} 页没有更多结果,结束提取")
                break
            
            print(f"找到 {len(result_links)} 个结果:\n")
            
            # 提取当前页面的结果
            for index, link in enumerate(result_links, 1):
                # 再次检查是否达到最大数量
                if len(all_results) >= MAX_RESULTS_PER_KEYWORD:
                    print(f"\n已达到本关键词最大爬取数量限制 ({MAX_RESULTS_PER_KEYWORD} 条),停止爬取")
                    break
                
                try:
                    title_text = link.text.strip()
                    href = link.get_attribute('href')
                    
                    if title_text and href:
                        # 去重检查
                        if href in seen_urls:
                            print(f"  跳过重复: {title_text}")
                            continue
                        
                        # 初始化正文内容为空字符串
                        detail_content = ""
                        
                        result_info = {
                            'page': page_num,
                            'index': (page_num - 1) * PER_PAGE + index,
                            'title': title_text,
                            'url': href,
                            'content': detail_content  # 添加正文内容
                        }
                        all_results.append(result_info)
                        seen_urls.add(href)
                        
                        global_index = (page_num - 1) * PER_PAGE + index
                        print(f"  {global_index}. {title_text}")
                        print(f"     URL: {href}")
                        
                        # 点击链接进入详情页 - 在当前窗口中打开
                        print(f"     正在打开详情页...")
                        
                        # 保存当前窗口句柄
                        main_window = driver.current_window_handle
                        
                        # 直接在当前窗口打开链接
                        driver.get(href)
                        time.sleep(3)  # 等待详情页加载
                        
                        # 检测详情页是否触发反爬
                        if check_anti_crawl(driver, consecutive_failures):
                            if not handle_anti_crawl_recovery(driver, consecutive_failures):
                                print(f"     ⚠ 详情页触发反爬,跳过此条数据")
                                consecutive_failures += 1
                                # 返回搜索结果页
                                driver.back()
                                time.sleep(3)
                                continue
                            else:
                                consecutive_failures = 0
                        
                        time.sleep(2)  # 等待详情页加载
                        
                        # 获取详情页信息
                        detail_url = driver.current_url
                        detail_title = driver.title
                        print(f"     详情页标题: {detail_title}")
                        print(f"     详情页URL: {detail_url}")
                        
                        # 提取详情页正文内容 - 根据实际页面结构
                        try:
                            # 等待正文内容加载 - 使用实际的类名 editor-content
                            WebDriverWait(driver, 5).until(
                                lambda d: d.find_element(By.CSS_SELECTOR, 'div.editor-content')
                            )
                            
                            # 获取正文div元素
                            content_div = driver.find_element(By.CSS_SELECTOR, 'div.editor-content')
                            
                            # 提取所有p标签的文本内容
                            paragraphs = content_div.find_elements(By.TAG_NAME, 'p')
                            content_parts = []
                            for p in paragraphs:
                                text = p.text.strip()
                                if text:
                                    content_parts.append(text)
                            
                            detail_content = '\n\n'.join(content_parts)
                            print(f"     正文字数: {len(detail_content)} 字符")
                            
                            # 更新结果中的正文内容
                            result_info['content'] = detail_content
                            
                            # 立即将这条数据写入文件 - 实时保存
                            try:
                                with open(output_filename, 'a', encoding='utf-8') as f:
                                    f.write(f"【关键词: {keyword} | 第 {global_index} 条】\n")
                                    f.write(f"标题: {title_text}\n")
                                    f.write(f"链接: {href}\n")
                                    f.write(f"{'-'*100}\n")
                                    
                                    if detail_content:
                                        f.write(f"正文内容:\n\n")
                                        f.write(detail_content)
                                        f.write(f"\n\n")
                                    
                                    f.write(f"{'='*100}\n\n")
                                
                                print(f"     ✓ 已保存到文件 (累计{len(all_results)}条)")
                            except Exception as write_err:
                                print(f"     ⚠ 写入文件失败: {write_err}")
                            
                        except Exception as e:
                            print(f"     提取正文时出错: {e}")
                        
                        # 返回搜索结果页
                        print(f"     返回搜索结果页...")
                        driver.back()
                        time.sleep(3)  # 等待返回
                        
                        # 重新等待页面加载完成 - 使用更通用的等待条件
                        try:
                            WebDriverWait(driver, 10).until(
                                lambda d: len(d.find_elements(By.TAG_NAME, 'a')) > 5
                            )
                        except:
                            pass
                        print(f"     已返回,继续下一个结果\n")
                        
                except Exception as e:
                    print(f"  处理第 {index} 个结果时出错: {e}")
                    consecutive_failures += 1
                    
                    # 检测是否触发反爬
                    if consecutive_failures >= 2:
                        if check_anti_crawl(driver, consecutive_failures - 1):
                            if handle_anti_crawl_recovery(driver, consecutive_failures):
                                consecutive_failures = 0
                    
                    # 尝试返回搜索结果页
                    try:
                        driver.back()
                        time.sleep(2)
                    except:
                        pass
                    continue
            
            # 检查是否因达到最大数量而中断内层循环
            if len(all_results) >= MAX_RESULTS_PER_KEYWORD:
                break
            
            print(f"\n第 {page_num} 页处理完成,已累计提取 {len(all_results)} 个标题")
            
            # 不再依赖TOTAL_RESULTS判断,而是通过检测是否有下一页按钮来决定是否继续
            # 如果已经提取完所有结果,退出循环 (这个条件保留作为备用)
            if TOTAL_RESULTS > 0 and len(all_results) >= TOTAL_RESULTS:
                print(f"\n已提取完所有 {len(all_results)} 个结果!")
                break
            
            # 尝试点击"下页"按钮 - 根据实际页面结构
            try:
                # 查找下一页按钮: <li>&gt;</li> (在ul的分页列表中)
                # HTML中显示为 &gt; 实体,但XPath中可以直接使用 > 或 contains
                next_page_selectors = [
                    "//div[@id='pagination']//li[contains(text(), '>')]",
                    "//ul//li[contains(text(), '>')]",
                    "//div[@class='pagination']//li[text()='>']",
                    "(//div[@id='pagination']//ul//li)[10]",  # 第10个li元素通常是">"
                ]
                
                next_button = None
                for selector in next_page_selectors:
                    try:
                        buttons = driver.find_elements(By.XPATH, selector)
                        if buttons:
                            print(f"\n使用选择器 '{selector}' 找到 {len(buttons)} 个候选按钮")
                            # 检查按钮是否可用(不是disabled状态)
                            for btn in buttons:
                                btn_text = btn.text.strip()
                                btn_class = btn.get_attribute('class') or ''
                                print(f"  按钮文本: '{btn_text}', 类名: '{btn_class}'")
                                
                                # 跳过disabled和active的按钮
                                if 'disabled' in btn_class or 'active' in btn_class:
                                    continue
                                
                                # 检查文本是否为 ">" 或包含 ">"
                                if btn_text == '>' or '>' in btn_text:
                                    next_button = btn
                                    print(f"  ✓ 找到下一页按钮!")
                                    break
                            
                            if next_button:
                                break
                    except Exception as e:
                        print(f"  选择器 '{selector}' 出错: {e}")
                        continue
                
                if next_button:
                    print("点击下一页按钮...")
                    # 使用JavaScript点击,更可靠
                    driver.execute_script("arguments[0].click();", next_button)
                    time.sleep(5)  # 等待页面加载完成
                    
                    # 检测翻页后是否触发反爬
                    if check_anti_crawl(driver, consecutive_failures):
                        if not handle_anti_crawl_recovery(driver, consecutive_failures):
                            print("翻页后触发反爬且恢复失败,停止爬取")
                            break
                        else:
                            consecutive_failures = 0
                    
                    # 验证是否成功翻页
                    WebDriverWait(driver, 10).until(
                        lambda d: len(d.find_elements(By.CSS_SELECTOR, 'a.textTitle')) > 0
                    )
                    
                    page_num += 1
                    print(f"成功翻到第 {page_num} 页")
                else:
                    print("\n未找到下一页按钮,已到达最后一页")
                    # 调试: 输出所有分页li元素
                    try:
                        all_li = driver.find_elements(By.XPATH, "//div[@id='pagination']//li")
                        print(f"分页区域共有 {len(all_li)} 个li元素:")
                        for i, li in enumerate(all_li):
                            print(f"  [{i}] 文本='{li.text.strip()}', 类='{li.get_attribute('class')}'")
                    except:
                        pass
                    break
                
            except Exception as e:
                print(f"翻页时出错: {e}")
                import traceback
                traceback.print_exc()
                consecutive_failures += 1
                
                # 检测是否触发反爬
                if consecutive_failures >= 2:
                    if check_anti_crawl(driver, consecutive_failures - 1):
                        if not handle_anti_crawl_recovery(driver, consecutive_failures):
                            print("翻页出错且触发反爬,停止爬取")
                            break
                
                print("已到达最后一页或翻页失败")
                break
        
        # 输出总结
        print(f"\n{'='*60}")
        print(f"关键词 '{keyword}' 提取完成!共提取到 {len(all_results)} 个标题")
        print(f"所有数据已实时保存到: {output_filename}")
        
        # 更新文件末尾,添加当前关键词的结束信息(不覆盖之前的内容)
        # try:
        #     with open(output_filename, 'a', encoding='utf-8') as f:
        #         # f.write(f"\n{'='*100}\n")
        #         # f.write(f"【关键词 '{keyword}' 爬取结束】\n")
        #         # f.write(f"结束时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        #         # f.write(f"本关键词总计爬取: {len(all_results)} 条数据\n")
        #         # f.write(f"{'='*100}\n\n")
        # except Exception as e:
        #     print(f"更新文件末尾信息失败: {e}")
        
        return all_results
        
    except KeyboardInterrupt:
        print(f"\n\n用户中断爬取!")
        print(f"关键词 '{keyword}' 已提取 {len(all_results)} 条数据并已实时保存到: {output_filename}")
        
        # 更新文件末尾
        try:
            with open(output_filename, 'a', encoding='utf-8') as f:
                f.write(f"\n{'='*100}\n")
                f.write(f"【关键词 '{keyword}' 爬取被中断】\n")
                f.write(f"中断时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"已爬取: {len(all_results)} 条数据\n")
                f.write(f"{'='*100}\n\n")
        except Exception as e:
            print(f"更新文件失败: {e}")
        
        return all_results
    except Exception as e:
        print(f"提取搜索结果标题时发生错误: {e}")
        import traceback
        traceback.print_exc()
        
        # 即使出错,也更新文件末尾信息
        try:
            with open(output_filename, 'a', encoding='utf-8') as f:
                f.write(f"\n{'='*100}\n")
                f.write(f"【关键词 '{keyword}' 爬取出错】\n")
                f.write(f"出错时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"已爬取: {len(all_results)} 条数据\n")
                f.write(f"错误信息: {str(e)}\n")
                f.write(f"{'='*100}\n\n")
        except Exception as file_err:
            print(f"更新文件失败: {file_err}")
        
        return all_results


def batch_search(driver, keywords, output_filename="住建部爬取结果.txt", resume=False):
    """
    批量搜索多个关键词
    
    参数:
        driver: Selenium WebDriver实例
        keywords: 关键词列表
        output_filename: 输出文件名
        resume: 是否从上次中断处继续爬取
    """
    total_count = 0
    success_count = 0
    failed_keywords = []
    
    # 进度文件路径
    progress_file = output_filename.replace('.txt', '_progress.json')
    
    # 如果启用续传,读取上次的进度
    start_index = 0
    if resume:
        try:
            import json
            if os.path.exists(progress_file):
                with open(progress_file, 'r', encoding='utf-8') as f:
                    progress_data = json.load(f)
                    start_index = progress_data.get('last_completed_index', 0)
                    print(f"\n{'#'*80}")
                    print(f"# 检测到进度文件,启用断点续传")
                    print(f"# 上次已完成: {start_index} 个关键词")
                    print(f"# 将从第 {start_index + 1} 个关键词继续爬取")
                    print(f"{'#'*80}\n")
                    
                    # 加载之前已统计的数据
                    total_count = progress_data.get('total_count', 0)
                    success_count = progress_data.get('success_count', 0)
                    failed_keywords = progress_data.get('failed_keywords', [])
            else:
                print(f"\n⚠️ 未找到进度文件,将从头开始爬取\n")
        except Exception as e:
            print(f"\n⚠️ 读取进度文件失败: {e},将从头开始爬取\n")
            start_index = 0
    
    print(f"\n{'#'*80}")
    print(f"# 开始批量搜索")
    if resume and start_index > 0:
        print(f"# 模式: 断点续传 (从第 {start_index + 1}/{len(keywords)} 个关键词继续)")
    else:
        print(f"# 模式: 全新爬取")
    print(f"# 总关键词数: {len(keywords)} 个")
    print(f"# 爬取策略: 每个关键词最多100条,不足则全量爬取")
    print(f"# 输出文件: {output_filename}")
    print(f"# 开始时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'#'*80}\n")
    
    # 如果不是续传,初始化输出文件
    if not resume or start_index == 0:
        try:
            with open(output_filename, 'w', encoding='utf-8') as f:
                f.write(f"住建部网站批量搜索结果\n")
                f.write(f"开始时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"关键词数量: {len(keywords)} 个\n")
                f.write(f"爬取策略: 每个关键词最多100条数据\n")
                f.write(f"{'='*100}\n\n")
            print(f"已创建输出文件: {output_filename}\n")
        except Exception as e:
            print(f"创建输出文件失败: {e}")
            return
    
    # 从指定的起始索引开始遍历
    for index in range(start_index, len(keywords)):
        keyword = keywords[index]
        
        print(f"\n{'*'*80}")
        print(f"* 进度: [{index + 1}/{len(keywords)}]")
        print(f"* 当前关键词: {keyword}")
        print(f"{'*'*80}")
        
        try:
            # 重置全局变量
            global TOTAL_RESULTS, PER_PAGE
            TOTAL_RESULTS = 0
            PER_PAGE = 10
            
            # 执行搜索 - 不限制页数,由extract_search_result_titles内部根据100条限制自动控制
            results = search(driver, keyword=keyword, output_filename=output_filename)
            
            if results:
                total_count += len(results)
                success_count += 1
                print(f"\n✓ 关键词 '{keyword}' 完成,提取 {len(results)} 条数据")
            else:
                print(f"\n⚠ 关键词 '{keyword}' 无结果或搜索失败")
                failed_keywords.append(keyword)
            
            # 每处理完一个关键词,保存进度
            try:
                import json
                progress_data = {
                    'last_completed_index': index + 1,  # 已完成的索引(下一个要处理的)
                    'total_count': total_count,
                    'success_count': success_count,
                    'failed_keywords': failed_keywords,
                    'last_keyword': keyword,
                    'timestamp': time.strftime('%Y-%m-%d %H:%M:%S')
                }
                with open(progress_file, 'w', encoding='utf-8') as f:
                    json.dump(progress_data, f, ensure_ascii=False, indent=2)
                print(f"   💾 进度已保存: 已完成 {index + 1}/{len(keywords)} 个关键词")
            except Exception as save_err:
                print(f"   ⚠️ 保存进度失败: {save_err}")
                
        except KeyboardInterrupt:
            print(f"\n\n⚠️ 用户中断程序!")
            print(f"已处理 {index + 1}/{len(keywords)} 个关键词")
            
            # 保存中断时的进度
            try:
                import json
                progress_data = {
                    'last_completed_index': index,  # 当前这个未完成,所以下次从这里开始
                    'total_count': total_count,
                    'success_count': success_count,
                    'failed_keywords': failed_keywords,
                    'last_keyword': keyword,
                    'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
                    'interrupted': True
                }
                with open(progress_file, 'w', encoding='utf-8') as f:
                    json.dump(progress_data, f, ensure_ascii=False, indent=2)
                print(f"💾 中断进度已保存到: {progress_file}")
                print(f"💡 下次运行时设置 RESUME=True 即可从断点继续")
            except Exception as save_err:
                print(f"⚠️ 保存进度失败: {save_err}")
            
            # 更新文件末尾信息
            try:
                with open(output_filename, 'a', encoding='utf-8') as f:
                    f.write(f"\n{'='*100}\n")
                    f.write(f"【爬取被用户中断】\n")
                    f.write(f"中断时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                    f.write(f"已处理关键词: {index + 1}/{len(keywords)}\n")
                    f.write(f"已成功: {success_count} 个\n")
                    f.write(f"已失败: {len(failed_keywords)} 个\n")
                    f.write(f"总数据量: {total_count} 条\n")
                    f.write(f"{'='*100}\n")
            except:
                pass
            
            raise  # 重新抛出异常,让上层处理
        
        except Exception as e:
            print(f"\n✗ 关键词 '{keyword}' 处理出错: {e}")
            failed_keywords.append(keyword)
            import traceback
            traceback.print_exc()
            
            # 记录失败信息到文件
            try:
                with open(output_filename, 'a', encoding='utf-8') as f:
                    f.write(f"\n{'='*100}\n")
                    f.write(f"【关键词 '{keyword}' 处理失败】\n")
                    f.write(f"失败时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                    f.write(f"错误信息: {str(e)}\n")
                    f.write(f"{'='*100}\n\n")
            except:
                pass
            
            # 即使失败也保存进度
            try:
                import json
                progress_data = {
                    'last_completed_index': index + 1,
                    'total_count': total_count,
                    'success_count': success_count,
                    'failed_keywords': failed_keywords,
                    'last_keyword': keyword,
                    'timestamp': time.strftime('%Y-%m-%d %H:%M:%S')
                }
                with open(progress_file, 'w', encoding='utf-8') as f:
                    json.dump(progress_data, f, ensure_ascii=False, indent=2)
            except:
                pass
            
            continue
    
    # 输出总结报告
    print(f"\n{'#'*80}")
    print(f"# 批量搜索完成!")
    print(f"# 总关键词数: {len(keywords)}")
    print(f"# 成功搜索: {success_count}")
    print(f"# 失败/无结果: {len(failed_keywords)}")
    print(f"# 总数据量: {total_count} 条")
    print(f"# 输出文件: {output_filename}")
    print(f"# 结束时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'#'*80}")
    
    if failed_keywords:
        print(f"\n失败/无结果的关键词:")
        for kw in failed_keywords:
            print(f"  - {kw}")
    
    # 写入最终总结到文件
    try:
        with open(output_filename, 'a', encoding='utf-8') as f:
            f.write(f"\n{'='*100}\n")
            f.write(f"批量搜索总结\n")
            f.write(f"{'='*100}\n")
            f.write(f"总关键词数: {len(keywords)}\n")
            f.write(f"成功搜索: {success_count}\n")
            f.write(f"失败/无结果: {len(failed_keywords)}\n")
            f.write(f"总数据量: {total_count} 条\n")
            f.write(f"结束时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            
            if failed_keywords:
                f.write(f"\n失败/无结果的关键词:\n")
                for kw in failed_keywords:
                    f.write(f"  - {kw}\n")
            
            f.write(f"{'='*100}\n")
    except Exception as e:
        print(f"写入总结信息失败: {e}")
    
    # 爬取完成后删除进度文件
    try:
        if os.path.exists(progress_file):
            os.remove(progress_file)
            print(f"\n✅ 爬取完成,已删除进度文件: {progress_file}")
    except Exception as e:
        print(f"\n⚠️ 删除进度文件失败: {e}")


if __name__ == "__main__":
    # ==================== 配置区域 ====================
    
    # 断点续传控制: True=从上次中断处继续, False=从头开始
    RESUME = True
    
    # 定义需要搜索的关键词列表
    keywords = [
        "隐患排查治理", "风险防控体系", "应急管理", "事件处置", "后期处置",
        "专项整治", "联合执法", "督导检查", "跟踪督办", "成果报告",
        "经验总结", "典型案例", "反面典型", "应急预案", "突发事件应急预案",
        "专项应急预案", "风险评估", "隐患排查", "突发事件应对", "灾情报告",
        "事故调查报告", "重大事故", "安全事故", "自然灾害", "公共卫生事件",
        "应急响应", "风险防控", "风险隐患", "应急处置", "灾后重建",
        "应急演练", "预案修订", "管理条例", "保护办法", "实施细则",
        "导则", "规范", "指导意见", "法律法规", "暂行办法",
        "试行办法", "标准规范", "技术规范", "地方性法规", "部门规章",
        "修订草案", "征求意见稿", "督查通报", "整改方案", "专项行动",
        "执法检查", "工作要点", "情况通报", "事故通报", "安全通报",
        "整改通报", "督查整改", "专项督查", "暗访督查", "问题整改",
        "整改报告", "调查报告", "处置情况报告", "挂牌督办", "限期整改",
        "约谈", "责任追究", "追责问责", "一案三查", "举一反三",
        "工作简报", "通知公告"
    ]
    
    output_file = "住建部爬取结果.txt"
    
    # ==================== 执行爬虫 ====================
    
    driver = open_website()
    if driver:
        try:
            batch_search(driver, keywords, output_file, resume=RESUME)
        except KeyboardInterrupt:
            print("\n\n用户中断程序!")
        except Exception as e:
            print(f"\n程序运行出错: {e}")
            import traceback
            traceback.print_exc()
        
        # 保持浏览器打开,让用户查看结果
        input("\n所有关键词搜索完毕，按回车键关闭浏览器...")
        driver.quit()
        print("浏览器已关闭")