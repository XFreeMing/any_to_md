"""Source-authored URL expectations, independent of extraction and writers."""

TITLE = '中文网页 / 安全标题 | 原站'
MARKERS = ('URL_BEGIN_950', 'URL_CELL_951', 'URL_SPAN_952', 'URL_END_953')
CODE = 'if value < 3:\n    print("中文")\n    print("done")'
HTML = f'''<!doctype html><html lang="zh"><head><title>{TITLE}</title>
<meta name="author" content="测试作者"><link rel="stylesheet" href="theme.css"></head>
<body><nav>NAV_EXCLUDED_954</nav><main><article><h1>中文文章</h1>
<p id="start">URL_BEGIN_950：这是源文件独立编写的正文，包含中文、图表与代码。阅读顺序应完整保留。</p>
<table><tr><th>项目</th><th>值</th></tr><tr><td>URL_CELL_951</td><td rowspan="2">URL_SPAN_952</td></tr><tr><td>第二行</td></tr></table>
<pre><code>if value &lt; 3:
    print("中文")
    print("done")</code></pre>
<img src="picture.png" alt="源像素" srcset="picture.png 1x, picture2.png 2x">
<p><a href="#start">正文起点</a> <a href="../reference?q=1">外部链接</a></p>
<aside>AD_EXCLUDED_955</aside><p>URL_END_953：文章结束，不能重复或遗漏。</p>
</article></main><footer>FOOTER_EXCLUDED_956</footer></body></html>'''
