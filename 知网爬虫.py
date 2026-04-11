
import requests
from jsonpath import jsonpath
from openpyxl import Workbook
import time
import random
from urllib.parse import quote


# 获取多页数据
def get_page_data(keyword, pages):
    # 定义汇总数据的列表
    page_data_list = []
    
    # URL编码关键词,用于Cookie
    encoded_keyword = quote(keyword)

    # 遍历抓取数据
    for page in range(pages):
        
        # 从第二页开始添加随机延迟,避免反爬
        if page > 0:
            delay = random.uniform(3, 6)  # 增加到3-6秒
            print(f"等待 {delay:.2f} 秒后继续爬取第 {page + 1} 页...")
            time.sleep(delay)

        headers = {
            'cookie': f'KEYWORD={encoded_keyword}; SID_search={random.randint(100000, 999999)}',
            "accept": "application/json, text/javascript, */*; q=0.01",
            "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
            "cache-control": "no-cache",
            "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
            "origin": "https://search.cnki.com.cn",
            "pragma": "no-cache",
            "priority": "u=1, i",
            "referer": "https://search.cnki.com.cn/Search/Result",
            "sec-ch-ua": '"Chromium";v="135", "Not-A.Brand";v="8"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
            "x-requested-with": "XMLHttpRequest"
        }

        # 定义请求来源
        url = "https://search.cnki.com.cn/api/search/listresult"

        # 定义表单参数
        data = {
            "searchType": "MulityTermsSearch",
            "ArticleType": "0",
            "ReSearch": "",
            "ParamIsNullOrEmpty": "false",
            "Islegal": "false",
            "Content": "",
            "Theme": keyword,
            "Title": "",
            "KeyWd": "",
            "Author": "",
            "SearchFund": "",
            "Originate": "",
            "Summary": "",
            "PublishTimeBegin": "",
            "PublishTimeEnd": "",
            "MapNumber": "",
            "Name": "",
            "Issn": "",
            "Cn": "",
            "Unit": "",
            "Public": "",
            "Boss": "",
            "FirstBoss": "",
            "Catalog": "",
            "Reference": "",
            "Speciality": "",
            "Type": "",
            "Subject": "",
            "SpecialityCode": "",
            "UnitCode": "",
            "Year": "",
            "AcefuthorFilter": "",
            "BossCode": "",
            "Fund": "",
            "Level": "",
            "Elite": "",
            "Organization": "",
            "Order": "1",
            "Page": f"{page}",
            "PageIndex": "",
            "ExcludeField": "",
            "ZtCode": "",
            "Smarts": ""
        }

        cookies = {
            "KEYWORD": encoded_keyword,
            "SID_search": str(random.randint(100000, 999999))
        }

        # 发起网络请求,带重试机制
        max_retries = 3
        retry_count = 0
        success = False
        
        while retry_count < max_retries and not success:
            try:
                # 发起网络请求
                response = requests.post(url, headers=headers, data=data, cookies=cookies, timeout=10)
                
                # 检查响应状态码
                if response.status_code == 403:
                    retry_count += 1
                    wait_time = random.uniform(5, 10) * retry_count  # 指数退避
                    print(f"⚠️ 第{page + 1}页被拦截(403),第{retry_count}次重试,等待{wait_time:.2f}秒...")
                    time.sleep(wait_time)
                    # 更新Cookie中的SID
                    cookies["SID_search"] = str(random.randint(100000, 999999))
                    headers['cookie'] = f'KEYWORD={encoded_keyword}; SID_search={cookies["SID_search"]}'
                    continue
                
                if response.status_code == 200:
                    success = True
                    print(f"✅ 第{page + 1}页请求成功")
                else:
                    print(f"❌ 第{page + 1}页请求失败,状态码: {response.status_code}")
                    break
                    
            except Exception as e:
                retry_count += 1
                print(f"❌ 第{page + 1}页请求异常: {str(e)},第{retry_count}次重试...")
                time.sleep(random.uniform(3, 6))
        
        if not success:
            print(f"⚠️ 第{page + 1}页爬取失败,跳过该页")
            continue
        
        try:
            # 筛选数据
            info_list = jsonpath(response.json(), '$.articleList[*]')
            
            if not info_list:
                print(f"⚠️ 第{page + 1}页未获取到数据")
                continue

            # 筛选具体信息
            for info in info_list:
                title = jsonpath(info, '$.title')[0]
                title = title.replace('~#@', '').replace('@#~', '')
                file_name = jsonpath(info, '$.fileName')[0]
                file_url = f'https://www.cnki.com.cn/Article/CJFDTOTAL-{file_name}.htm'
                summary = jsonpath(info, '$.summary')[0]
                author = jsonpath(info, '$.author')[0]
                originate = jsonpath(info, '$.originate')[0]
                publish_pyname = jsonpath(info, '$.publishPYName')[0]
                publish_time = jsonpath(info, '$.publishTime')[0]
                arcitle_type = jsonpath(info, '$.arcitleType')[0]
                download_count = jsonpath(info, '$.downloadCount')[0]
                quote_count = jsonpath(info, '$.quoteCount')[0]
                key_word = jsonpath(info, '$.keyWord')[0]
                dbtype = jsonpath(info, '$.dbType')[0]
                cd_no = jsonpath(info, '$.cDNo')[0]
                author_code = jsonpath(info, '$.authorCode')[0]
                dept_code = jsonpath(info, '$.deptCode')[0]
                db_name = jsonpath(info, '$.dbName')[0]
                db_source = jsonpath(info, '$.dbSource')[0]
                allow_download = jsonpath(info, '$.allowDownload')[0]
                zt_code = jsonpath(info, '$.zTCode')[0]

                # 汇总每条数据
                page_data_list.append(
                    [title, file_url, summary, author, originate, publish_pyname, publish_time, arcitle_type,
                     download_count, quote_count, key_word, dbtype, cd_no, author_code, dept_code, db_name,
                     db_source, allow_download, zt_code])
            
            print(f"📊 第{page + 1}页获取 {len(info_list)} 条数据")
            
        except Exception as e:
            print(f"❌ 第{page + 1}页数据解析失败: {str(e)}")
            continue

    # 返回抓取页数的汇总数据
    return page_data_list


def write_to_excel(info, search_word):
    # 创建工作簿对象
    wb = Workbook()

    # 选择当前的工作表
    ws = wb.active

    # 设置工作表的标题名称
    ws.title = search_word[:31]  # Excel工作表名最长31字符

    # 定义列表保存工作表的第一行数据
    title = ['title', 'fileUrl', 'summary', 'author', 'originate', 'publishPYName', 'publishTime', 'arcitleType',
             'downloadCount', 'quoteCount', 'keyWord', 'dbType', 'cDNo', 'authorCode', 'deptCode', 'dbName', 'dbSource',
             'allowDownload', 'zTCode']

    # 将定义的数据添加的第一行中
    ws.append(title)

    # 循环遍历传入的数据
    for row in info:
        # 清理数据中的无效Unicode字符
        cleaned_row = []
        for cell in row:
            if isinstance(cell, str):
                # 移除代理对字符和其他无效Unicode
                cell = cell.encode('utf-8', errors='ignore').decode('utf-8')
                # 限制单元格长度(Excel限制32767字符)
                cell = cell[:32767] if len(cell) > 32767 else cell
            cleaned_row.append(cell)
        
        # 遍历出这组数据中每条数据的信息,并添加到一行中
        ws.append(cleaned_row)
    
    # 将添加好的数据保存到Excel文件中
    try:
        wb.save(f'{search_word}.xlsx')
        print(f"✅ 文件保存成功: {search_word}.xlsx")
    except Exception as e:
        # 如果保存失败,尝试使用CSV格式
        csv_filename = f'{search_word}.csv'
        import csv
        with open(csv_filename, 'w', encoding='utf-8-sig', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(title)
            writer.writerows(info)
        print(f"⚠️ Excel保存失败,已转为CSV格式: {csv_filename}")
        print(f"错误信息: {str(e)}")


if __name__ == '__main__':
    keyword = input('请输入抓取的关键词：')
    pages = int(input('请输入抓取的页数：'))

    # 抓取页面中的数据
    page_data_list = get_page_data(keyword, pages)

    # 将筛选出的数据写入到Excel表格中
    write_to_excel(page_data_list, keyword)
    
    print(f"\n✅ 爬取完成!共获取 {len(page_data_list)} 条数据,已保存到 {keyword}.xlsx")

