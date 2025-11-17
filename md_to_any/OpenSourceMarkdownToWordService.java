package com.baiying.ai.mcpplatformapi.md_to_any.service;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.net.HttpURLConnection;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Objects;

import org.apache.poi.ooxml.POIXMLDocumentPart;
import org.apache.poi.openxml4j.opc.PackageRelationship;
import org.apache.poi.openxml4j.opc.PackageRelationshipTypes;
import org.apache.poi.xwpf.usermodel.XWPFDocument;
import org.apache.poi.xwpf.usermodel.XWPFParagraph;
import org.apache.poi.xwpf.usermodel.XWPFRun;
import org.jetbrains.annotations.NotNull;
import org.jsoup.Jsoup;
import org.jsoup.nodes.Document;
import org.jsoup.nodes.Element;
import org.jsoup.select.Elements;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import com.baiying.ai.mcpplatformapi.common.client.attchment.AttachmentClient;
import com.baiying.ai.mcpplatformapi.md_to_any.MdToAnyIO;

import lombok.extern.slf4j.Slf4j;

/**
 * 开源方案 Markdown 转 Word 服务
 */
@Service
@Slf4j
public class OpenSourceMarkdownToWordService extends AbstractMarkdownToAnyService {

    public OpenSourceMarkdownToWordService(
            AttachmentClient attachmentClient,
            EmojiConfigLoader emojiConfigLoader,
            @Value("${md_to_any.template_url:https://smbfiletest.oss-cn-beijing.aliyuncs.com/md_to_any_template/md_to_html.html}") String templateUrl) {
        super(attachmentClient, emojiConfigLoader, templateUrl);
    }

    @Override
    public MdToAnyIO.Result convertMarkdown(String aiCodingId, String mdContent, String fileName) throws Exception {
        log.info("=== 开始开源方案 Markdown 转 Word 流程（Emoji资源）===\n文件名: {}", fileName);

        Path tempDir = null;
        try {
            // 1. 创建临时目录
            tempDir = Files.createTempDirectory("markdown_word_" + System.currentTimeMillis());
            log.info("✓ 创建临时目录: {}", tempDir);

            // 2. 将 Markdown 转换为 HTML（启用Emoji扩展）
            log.info("步骤 1: 转换 Markdown 为 HTML（包含Emoji处理）...");
            String html = convertMarkdownToHtmlWithEmoji(mdContent);
            String htmlUrl = attachmentClient.uploadStringAsFile(aiCodingId, "md_to_any", html, fileName + ".html");

            log.info("✓ Markdown 转 HTML 成功，HTML 长度: {} 字符", html.length());

            // 3. 处理HTML中的Emoji，转换为内嵌的Base64图片
            log.info("步骤 2: 处理HTML中的Emoji（使用本地资源）...");
            String processedHtml = processEmojiInHtml(html);

            // 4. 处理HTML中的WebP图片，转换为PNG格式（base64）
            log.info("步骤 3: 处理HTML中的WebP图片（转换为PNG）...");
            String webpProcessedHtml = processWebPImagesInHtml(processedHtml);

            // 5. 将 HTML 转换为 Word 字节数组
            log.info("步骤 4: 转换 HTML 为 Word...");
            byte[] wordBytes = convertHtmlToWord(webpProcessedHtml);
            log.info("✓ HTML 转 Word 成功，Word 大小: {} 字节", wordBytes.length);

            // 6. 上传到 OSS 获取 URL
            log.info("步骤 5: 上传到 OSS...");
            String wordFileName = fileName + ".docx";
            String ossUrl = attachmentClient.uploadUserFile(wordBytes, aiCodingId, "md_to_any", wordFileName);

            log.info("✓ 上传到 OSS 成功，URL: {}", ossUrl);

            log.info("=== 开源方案 Markdown 转 Word 流程完成 ===");

            return new MdToAnyIO.Result(ossUrl, htmlUrl);

        } catch (Exception e) {
            log.error("Markdown 转 Word 过程中发生错误: {}", e.getMessage(), e);
            throw new RuntimeException("Markdown 转 Word 失败: " + e.getMessage(), e);
        } finally {
            cleanupTempFiles(tempDir);
        }
    }

    /**
     * 将 HTML 转换为 Word 字节数组
     */
    private byte[] convertHtmlToWord(String html) {
        try (ByteArrayOutputStream outputStream = new ByteArrayOutputStream();
                XWPFDocument document = new XWPFDocument()) {

            // 使用 JSoup 解析 HTML
            Document doc = Jsoup.parse(html);
            Element body = doc.body();

            // 检测并移除水印内容（稍后作为水印添加）
            String watermarkText = extractWatermarkText(body);

            // 处理 HTML 元素
            processHtmlElements(body, document);

            // 如果检测到水印文本，添加到文档
            if (watermarkText != null && !watermarkText.isEmpty()) {
                addWatermark(document, watermarkText);
            }

            // 写入文档
            document.write(outputStream);
            return outputStream.toByteArray();

        } catch (Exception e) {
            log.error("HTML 转 Word 失败: {}", e.getMessage(), e);
            throw new RuntimeException("HTML 转 Word 失败", e);
        }
    }

    // ==================== Word 相关公共方法 ====================

    /**
     * 设置中文字体（同时设置西文和东亚字体）
     */
    private void setChineseFont(XWPFRun run, String fontFamily, int fontSize) {
        run.setFontSize(fontSize);
        run.setFontFamily(fontFamily);
        
        // 使用底层 XML API 设置东亚字体（确保中文正确显示）
        try {
            org.openxmlformats.schemas.wordprocessingml.x2006.main.CTR ctr = run.getCTR();
            org.openxmlformats.schemas.wordprocessingml.x2006.main.CTRPr rPr = ctr.getRPr();
            if (rPr == null) {
                rPr = ctr.addNewRPr();
            }
            
            org.openxmlformats.schemas.wordprocessingml.x2006.main.CTFonts fonts = rPr.addNewRFonts();
            
            // 设置各种字体（确保覆盖所有场景）
            fonts.setAscii(fontFamily);      // ASCII 字符
            fonts.setHAnsi(fontFamily);      // High ANSI 字符
            fonts.setEastAsia(fontFamily);   // 东亚字符（中日韩）
            fonts.setCs(fontFamily);         // 复杂脚本字符
        } catch (Exception e) {
            // 如果设置东亚字体失败，至少保证基本字体设置成功
            log.debug("设置东亚字体时出现异常（已忽略）: {}", e.getMessage());
        }
    }

    /**
     * 创建超链接关系
     */
    private String createHyperlink(XWPFParagraph paragraph, String url) {
        try {
            // 获取段落所在的文档部分
            POIXMLDocumentPart part = paragraph.getBody().getPart();
            
            // 创建外部超链接关系
            PackageRelationship rel = part.getPackagePart().addExternalRelationship(
                url,
                PackageRelationshipTypes.HYPERLINK_PART
            );
            
            return rel.getId();
        } catch (Exception e) {
            log.error("创建超链接失败: {}", e.getMessage(), e);
            return "";
        }
    }

    /**
     * 递归处理 HTML 元素并转换为 Word 段落
     */
    private void processHtmlElements(Element element, XWPFDocument document) {
        for (Element child : element.children()) {
            String tagName = child.tagName().toLowerCase();

            switch (tagName) {
                case "h1", "h2", "h3", "h4", "h5", "h6" -> addHeading(document, tagName, child);
                case "p" -> addParagraph(document, child);
                case "ul", "ol" -> addList(document, child, tagName.equals("ol"));
                case "table" -> addTable(document, child);
                case "pre" -> addCodeBlock(document, child);
                case "blockquote" -> addBlockquote(document, child);
                case "hr" -> addHorizontalRule(document);
                case "br" -> document.createParagraph();
                case "div" ->
                    // 递归处理 div 的子元素
                        processHtmlElements(child, document);
                default -> {
                    // 如果是其他块级元素或有子元素，递归处理
                    if (child.children().isEmpty() && !child.text().trim().isEmpty()) {
                        addParagraph(document, child);
                    } else if (!child.children().isEmpty()) {
                        processHtmlElements(child, document);
                    }
                }
            }
        }
    }

    /**
     * 添加标题（与模板样式统一）
     */
    private void addHeading(XWPFDocument document, String level, Element element) {
        XWPFParagraph paragraph = document.createParagraph();

        int fontSize = switch (level) {
            case "h1" -> 18;
            case "h2" -> 16;
            case "h3" -> 14;
            case "h4" -> 13;
            default -> 12;
        };

        paragraph.setSpacingBefore(240);
        paragraph.setSpacingAfter(120);
        paragraph.setSpacingBetween(1.15, org.apache.poi.xwpf.usermodel.LineSpacingRule.AUTO);

        // 使用 processNode 来处理标题中可能包含的格式化文本
        processHeadingNode(paragraph, element, fontSize);
    }

    /**
     * 处理标题节点
     */
    private void processHeadingNode(XWPFParagraph paragraph, org.jsoup.nodes.Node node, int fontSize) {
        if (node instanceof org.jsoup.nodes.TextNode textNode) {
            String text = textNode.text();
            if (!text.isEmpty()) {
                XWPFRun run = paragraph.createRun();
                run.setText(text);
                setChineseFont(run, "Microsoft YaHei", fontSize);
                run.setBold(true);
                run.setColor("333333");
            }
        } else if (node instanceof Element elem) {
            String tagName = elem.tagName().toLowerCase();
            // 处理标题中的图片
            if ("img".equals(tagName)) {
                String src = elem.attr("src");
                String alt = elem.attr("alt");
                String className = elem.attr("class");
                boolean isEmoji = className != null && className.contains("is-emoji");
                
                if (!src.isEmpty()) {
                    try {
                        if (isEmoji) {
                            // Emoji 图片特殊处理
                            addEmojiImageToParagraph(paragraph, src, alt);
                        } else {
                            // 标题中的图片不居中
                            addImageToParagraph(paragraph, src, alt, false);
                        }
                    } catch (Exception e) {
                        log.warn("标题中添加图片失败 [{}]: {}", src, e.getMessage());
                        // 如果图片插入失败，添加替代文本
                        if (!alt.isEmpty()) {
                            XWPFRun altRun = paragraph.createRun();
                            altRun.setText("[图片: " + alt + "]");
                            setChineseFont(altRun, "Microsoft YaHei", fontSize);
                            altRun.setColor("999999");
                            altRun.setBold(true);
                            altRun.setItalic(true);
                        }
                    }
                }
            } else {
                // 递归处理其他子节点
                for (org.jsoup.nodes.Node child : elem.childNodes()) {
                    processHeadingNode(paragraph, child, fontSize);
                }
            }
        }
    }

    /**
     * 处理表格单元格节点（支持图片、文本格式化等）
     */
    private void processTableCellNode(XWPFParagraph paragraph, org.jsoup.nodes.Node node, boolean isHeaderRow) {
        processTableCellNode(paragraph, node, isHeaderRow, null);
    }

    /**
     * 处理表格单元格节点（支持图片、文本格式化等）
     * @param paragraph 段落
     * @param node 节点
     * @param isHeaderRow 是否表头
     * @param tableCell 表格单元格（用于计算图片尺寸，可为 null）
     */
    private void processTableCellNode(XWPFParagraph paragraph, org.jsoup.nodes.Node node, boolean isHeaderRow, 
                                     org.apache.poi.xwpf.usermodel.XWPFTableCell tableCell) {
        int fontSize = 11; // 表格单元格字体大小
        boolean isBold = isHeaderRow; // 表头默认粗体
        
        if (node instanceof org.jsoup.nodes.TextNode textNode) {
            String text = textNode.text();
            if (!text.isEmpty()) {
                XWPFRun run = paragraph.createRun();
                run.setText(text);
                setChineseFont(run, "Microsoft YaHei", fontSize);
                run.setColor(isHeaderRow ? "FFFFFF" : "333333");
                if (isBold) run.setBold(true);
            }
        } else if (node instanceof Element elem) {
            String tagName = elem.tagName().toLowerCase();
            
            // 处理图片
            if ("img".equals(tagName)) {
                String src = elem.attr("src");
                String alt = elem.attr("alt");
                String className = elem.attr("class");
                boolean isEmoji = className != null && className.contains("is-emoji");
                
                if (!src.isEmpty()) {
                    try {
                        if (isEmoji) {
                            // Emoji 图片特殊处理：1em 大小，内联显示
                            addEmojiImageToParagraph(paragraph, src, alt);
                        } else if (tableCell != null) {
                            // 如果表格单元格不为空，使用适应单元格大小的图片
                            addImageToTableCell(paragraph, src, alt, tableCell);
                        } else {
                            addImageToParagraph(paragraph, src, alt, false);
                        }
                    } catch (Exception e) {
                        log.warn("表格单元格中添加图片失败 [{}]: {}", src, e.getMessage());
                        // 如果图片插入失败，添加替代文本
                        if (!alt.isEmpty()) {
                            XWPFRun altRun = paragraph.createRun();
                            altRun.setText("[图片: " + alt + "]");
                            setChineseFont(altRun, "Microsoft YaHei", fontSize);
                            altRun.setColor(isHeaderRow ? "FFFFFF" : "999999");
                            altRun.setItalic(true);
                            if (isBold) altRun.setBold(true);
                        }
                    }
                }
            } else {
                // 递归处理其他子节点
                for (org.jsoup.nodes.Node child : elem.childNodes()) {
                    processTableCellNode(paragraph, child, isHeaderRow, tableCell);
                }
            }
        }
    }

    /**
     * 添加段落（与模板样式统一）
     */
    private void addParagraph(XWPFDocument document, Element element) {
        XWPFParagraph paragraph = document.createParagraph();

        paragraph.setSpacingAfter(120);
        paragraph.setSpacingBetween(1.15, org.apache.poi.xwpf.usermodel.LineSpacingRule.AUTO);

        addFormattedText(paragraph, element);
    }

    /**
     * 添加格式化的文本（支持粗体、斜体、链接等，与模板样式统一）
     */
    private void addFormattedText(XWPFParagraph paragraph, Element element) {
        processNode(paragraph, element, false, false, false);
    }

    /**
     * 递归处理节点，正确应用格式
     */
    private void processNode(XWPFParagraph paragraph, org.jsoup.nodes.Node node, 
                            boolean bold, boolean italic, boolean underline) {
        if (node instanceof org.jsoup.nodes.TextNode textNode) {
            // 处理文本节点
            String text = textNode.text();
            if (!text.isEmpty()) {
                XWPFRun run = paragraph.createRun();
                run.setText(text);
                setChineseFont(run, "Microsoft YaHei", 12);
                run.setColor("333333");
                
                if (bold) run.setBold(true);
                if (italic) run.setItalic(true);
                if (underline) run.setUnderline(org.apache.poi.xwpf.usermodel.UnderlinePatterns.SINGLE);
            }
        } else if (node instanceof Element elem) {
            String tagName = elem.tagName().toLowerCase();
            
            // 根据标签更新格式状态
            boolean newBold = bold || tagName.equals("strong") || tagName.equals("b");
            boolean newItalic = italic || tagName.equals("em") || tagName.equals("i");
            boolean newUnderline = underline || tagName.equals("u");
            
            // 特殊处理某些元素
            switch (tagName) {
                case "a" -> {
                    // 处理超链接（创建真正的可点击链接）
                    String linkText = elem.text();
                    String href = elem.attr("href");
                    
                    if (!href.isEmpty()) {
                        // 创建超链接
                        String rId = createHyperlink(paragraph, href);
                        
                        // 创建超链接的 run
                        XWPFRun linkRun = paragraph.createRun();
                        linkRun.setText(linkText);
                        setChineseFont(linkRun, "Microsoft YaHei", 12);
                        linkRun.setColor("0563C1");  // 蓝色
                        linkRun.setUnderline(org.apache.poi.xwpf.usermodel.UnderlinePatterns.SINGLE);
                        if (newBold) linkRun.setBold(true);
                        if (newItalic) linkRun.setItalic(true);
                        
                        // 将 run 标记为超链接
                        org.openxmlformats.schemas.wordprocessingml.x2006.main.CTHyperlink ctHyperlink = 
                            paragraph.getCTP().addNewHyperlink();
                        ctHyperlink.setId(rId);
                        ctHyperlink.addNewR();
                        
                        // 将现有 run 的内容复制到超链接中
                        org.openxmlformats.schemas.wordprocessingml.x2006.main.CTR ctr = linkRun.getCTR();
                        ctHyperlink.setRArray(new org.openxmlformats.schemas.wordprocessingml.x2006.main.CTR[]{ctr});
                        
                        // 从段落中移除原 run（因为已经添加到超链接中了）
                        paragraph.getCTP().removeR(paragraph.getCTP().sizeOfRArray() - 1);
                    } else {
                        // 如果没有 href，就作为普通文本
                        XWPFRun textRun = paragraph.createRun();
                        textRun.setText(linkText);
                        setChineseFont(textRun, "Microsoft YaHei", 12);
                        textRun.setColor("333333");
                        if (newBold) textRun.setBold(true);
                        if (newItalic) textRun.setItalic(true);
                    }
                }
                case "img" -> {
                    // 处理图片
                    String src = elem.attr("src");
                    String alt = elem.attr("alt");
                    String className = elem.attr("class");
                    boolean isEmoji = className != null && className.contains("is-emoji");
                    
                    if (!src.isEmpty()) {
                        try {
                            if (isEmoji) {
                                // Emoji 图片特殊处理：1em 大小，内联显示
                                addEmojiImageToParagraph(paragraph, src, alt);
                            } else {
                                addImageToParagraph(paragraph, src, alt);
                            }
                        } catch (Exception e) {
                            log.warn("添加图片失败 [{}]: {}", src, e.getMessage());
                            // 如果图片插入失败，添加替代文本
                            if (!alt.isEmpty()) {
                                XWPFRun altRun = paragraph.createRun();
                                altRun.setText("[图片: " + alt + "]");
                                setChineseFont(altRun, "Microsoft YaHei", 11);
                                altRun.setColor("999999");
                                altRun.setItalic(true);
                            }
                        }
                    }
                }
                case "code" -> {
                    // 内联代码
                    XWPFRun codeRun = paragraph.createRun();
                    codeRun.setText(elem.text());
                    setChineseFont(codeRun, "Courier New", 11);
                    codeRun.setColor("D73A49");
                    if (newBold) codeRun.setBold(true);
                    if (newItalic) codeRun.setItalic(true);
                }
                case "br" ->
                    // 换行
                        paragraph.createRun().addBreak();
                default -> {
                    // 递归处理子节点
                    for (org.jsoup.nodes.Node child : elem.childNodes()) {
                        processNode(paragraph, child, newBold, newItalic, newUnderline);
                    }
                }
            }
        }
    }

    /**
     * 添加列表（与模板样式统一）
     * @param document Word 文档
     * @param listElement 列表元素（ul 或 ol）
     * @param ordered 是否有序列表
     * @param indentLevel 缩进级别（用于嵌套列表，从 0 开始）
     */
    private void addList(XWPFDocument document, Element listElement, boolean ordered) {
        addList(document, listElement, ordered, 0);
    }

    /**
     * 添加列表（与模板样式统一，支持嵌套）
     * @param document Word 文档
     * @param listElement 列表元素（ul 或 ol）
     * @param ordered 是否有序列表
     * @param indentLevel 缩进级别（用于嵌套列表，从 0 开始）
     */
    private void addList(XWPFDocument document, Element listElement, boolean ordered, int indentLevel) {
        // 直接获取所有直接子 li 元素
        Elements items = listElement.select("> li");
        
        // 如果没找到，尝试直接从 children 中获取 li 元素
        if (items.isEmpty()) {
            items = new Elements();
            for (Element child : listElement.children()) {
                if (child.tagName().equalsIgnoreCase("li")) {
                    items.add(child);
                }
            }
        }
        
        // 如果还是空的，记录日志
        if (items.isEmpty()) {
            log.warn("列表中未找到 li 元素: {}", listElement.html());
            return;
        }
        
        // 计算缩进：基础缩进 360，每级嵌套增加 360
        int baseIndent = 360;
        int currentIndent = baseIndent + (indentLevel * 360);
        
        for (int i = 0; i < items.size(); i++) {
            Element item = items.get(i);
            XWPFParagraph paragraph = document.createParagraph();

            paragraph.setIndentationLeft(currentIndent);
            paragraph.setSpacingBetween(1.15, org.apache.poi.xwpf.usermodel.LineSpacingRule.AUTO);
            paragraph.setSpacingAfter(80);

            // 添加列表前缀
            XWPFRun prefixRun = paragraph.createRun();
            String prefix = ordered ? (i + 1) + ". " : "• ";
            prefixRun.setText(prefix);
            setChineseFont(prefixRun, "Microsoft YaHei", 12);
            prefixRun.setColor("333333");

            // 先处理列表项的直接内容（文本、格式化、br 等），但不处理嵌套列表
            // 将子节点分组：文本节点和格式节点先处理，嵌套列表后处理
            java.util.List<org.jsoup.nodes.Node> textNodes = new java.util.ArrayList<>();
            java.util.List<Element> nestedLists = new java.util.ArrayList<>();
            
            for (org.jsoup.nodes.Node child : item.childNodes()) {
                if (child instanceof Element childElem) {
                    String tagName = childElem.tagName().toLowerCase();
                    if (tagName.equals("ul") || tagName.equals("ol")) {
                        // 嵌套列表，稍后处理
                        nestedLists.add(childElem);
                    } else {
                        // 其他元素（包括 br），先处理
                        textNodes.add(child);
                    }
                } else {
                    // 文本节点，先处理
                    textNodes.add(child);
                }
            }
            
            // 先处理文本和格式化内容（包括 br）
            for (org.jsoup.nodes.Node child : textNodes) {
                processNode(paragraph, child, false, false, false);
            }
            
            // 然后再处理嵌套列表（递归调用，增加缩进级别）
            for (Element nestedList : nestedLists) {
                addList(document, nestedList, nestedList.tagName().equalsIgnoreCase("ol"), indentLevel + 1);
            }
        }
    }

    /**
     * 添加表格（现代美观设计）
     */
    private void addTable(XWPFDocument document, Element tableElement) {
        Elements rows = tableElement.select("tr");
        if (rows.isEmpty()) {
            return;
        }

        Elements firstRowCells = Objects.requireNonNull(rows.first()).select("th, td");
        int colCount = firstRowCells.size();
        if (colCount == 0) {
            return;
        }
        org.apache.poi.xwpf.usermodel.XWPFTable table = document.createTable(rows.size(), colCount);

        // 设置表格整体样式
        table.setWidth("100%");
        
        // 设置表格间距（表格前后留白）
        table.setCellMargins(200, 200, 200, 200);
        
        // 设置表格不跨页分割
        setTableKeepTogether(table);

        int rowIndex = 0;
        for (Element row : rows) {
            Elements cells = row.select("th, td");
            org.apache.poi.xwpf.usermodel.XWPFTableRow tableRow = table.getRow(rowIndex);
            
            // 设置行高
            tableRow.setHeight(500);
            
            // 设置每行不能跨页断开
            setRowKeepTogether(tableRow);

            boolean isHeaderRow = Objects.requireNonNull(cells.first()).tagName().equalsIgnoreCase("th");

            int cellIndex = 0;
            for (Element cell : cells) {
                if (cellIndex < tableRow.getTableCells().size()) {
                    org.apache.poi.xwpf.usermodel.XWPFTableCell tableCell = tableRow.getCell(cellIndex);

                    // 垂直居中对齐
                    tableCell.setVerticalAlignment(org.apache.poi.xwpf.usermodel.XWPFTableCell.XWPFVertAlign.CENTER);

                    org.openxmlformats.schemas.wordprocessingml.x2006.main.CTTcPr tcPr = tableCell.getCTTc().getTcPr();
                    if (tcPr == null) {
                        tcPr = tableCell.getCTTc().addNewTcPr();
                    }
                    
                    // 设置单元格内边距（更大的内边距使表格更舒适）
                    org.openxmlformats.schemas.wordprocessingml.x2006.main.CTTcMar cellMar = tcPr.addNewTcMar();

                    org.openxmlformats.schemas.wordprocessingml.x2006.main.CTTblWidth topMar = cellMar.addNewTop();
                    topMar.setW(java.math.BigInteger.valueOf(200));  // 增加上边距
                    topMar.setType(org.openxmlformats.schemas.wordprocessingml.x2006.main.STTblWidth.DXA);

                    org.openxmlformats.schemas.wordprocessingml.x2006.main.CTTblWidth bottomMar = cellMar.addNewBottom();
                    bottomMar.setW(java.math.BigInteger.valueOf(200));  // 增加下边距
                    bottomMar.setType(org.openxmlformats.schemas.wordprocessingml.x2006.main.STTblWidth.DXA);

                    org.openxmlformats.schemas.wordprocessingml.x2006.main.CTTblWidth leftMar = cellMar.addNewLeft();
                    leftMar.setW(java.math.BigInteger.valueOf(180));  // 增加左边距
                    leftMar.setType(org.openxmlformats.schemas.wordprocessingml.x2006.main.STTblWidth.DXA);

                    org.openxmlformats.schemas.wordprocessingml.x2006.main.CTTblWidth rightMar = cellMar.addNewRight();
                    rightMar.setW(java.math.BigInteger.valueOf(180));  // 增加右边距
                    rightMar.setType(org.openxmlformats.schemas.wordprocessingml.x2006.main.STTblWidth.DXA);

                    if (!tableCell.getParagraphs().isEmpty()) {
                        XWPFParagraph cellPara = tableCell.getParagraphs().getFirst();
                        
                        // 表头居中，数据行左对齐
                        if (isHeaderRow) {
                            cellPara.setAlignment(org.apache.poi.xwpf.usermodel.ParagraphAlignment.CENTER);
                        } else {
                            cellPara.setAlignment(org.apache.poi.xwpf.usermodel.ParagraphAlignment.LEFT);
                        }
                        
                        cellPara.setSpacingBetween(1.2, org.apache.poi.xwpf.usermodel.LineSpacingRule.AUTO);

                        // 处理单元格中的格式化内容（包括图片）
                        for (org.jsoup.nodes.Node child : cell.childNodes()) {
                            processTableCellNode(cellPara, child, isHeaderRow, tableCell);
                        }

                        // 设置背景色和文字样式
                        org.openxmlformats.schemas.wordprocessingml.x2006.main.CTShd shd;
                        if (tcPr.getShd() != null) {
                            shd = tcPr.getShd();
                        } else {
                            shd = tcPr.addNewShd();
                        }

                        // 设置所有文本 runs 的颜色和样式
                        if (isHeaderRow) {
                            // 表头：深色背景 + 白色粗体文字
                            shd.setFill("4472C4");  // 深蓝色
                            for (XWPFRun run : cellPara.getRuns()) {
                                run.setColor("FFFFFF");  // 白色文字
                                run.setBold(true);
                            }
                        } else if (rowIndex % 2 == 1) {
                            // 奇数行：浅灰色背景
                            shd.setFill("F2F2F2");
                            for (XWPFRun run : cellPara.getRuns()) {
                                run.setColor("333333");
                            }
                        } else {
                            // 偶数行：白色背景
                            shd.setFill("FFFFFF");
                            for (XWPFRun run : cellPara.getRuns()) {
                                run.setColor("333333");
                            }
                        }

                        // 设置边框（更细致的边框设计）
                        org.openxmlformats.schemas.wordprocessingml.x2006.main.CTTcBorders borders = tcPr.addNewTcBorders();
                        
                        // 边框粗细：4pt（更细致）
                        java.math.BigInteger borderSize = java.math.BigInteger.valueOf(4);
                        
                        // 顶部边框
                        org.openxmlformats.schemas.wordprocessingml.x2006.main.CTBorder topBorder = borders.addNewTop();
                        topBorder.setVal(org.openxmlformats.schemas.wordprocessingml.x2006.main.STBorder.SINGLE);
                        topBorder.setSz(isHeaderRow ? java.math.BigInteger.valueOf(8) : borderSize);
                        topBorder.setColor(isHeaderRow ? "4472C4" : "D0D0D0");

                        // 底部边框
                        org.openxmlformats.schemas.wordprocessingml.x2006.main.CTBorder bottomBorder = borders.addNewBottom();
                        bottomBorder.setVal(org.openxmlformats.schemas.wordprocessingml.x2006.main.STBorder.SINGLE);
                        bottomBorder.setSz(isHeaderRow ? java.math.BigInteger.valueOf(8) : borderSize);
                        bottomBorder.setColor(isHeaderRow ? "4472C4" : "D0D0D0");

                        // 左边框
                        org.openxmlformats.schemas.wordprocessingml.x2006.main.CTBorder leftBorder = borders.addNewLeft();
                        leftBorder.setVal(org.openxmlformats.schemas.wordprocessingml.x2006.main.STBorder.SINGLE);
                        leftBorder.setSz(borderSize);
                        leftBorder.setColor("E0E0E0");

                        // 右边框
                        org.openxmlformats.schemas.wordprocessingml.x2006.main.CTBorder rightBorder = borders.addNewRight();
                        rightBorder.setVal(org.openxmlformats.schemas.wordprocessingml.x2006.main.STBorder.SINGLE);
                        rightBorder.setSz(borderSize);
                        rightBorder.setColor("E0E0E0");
                    }
                }
                cellIndex++;
            }
            rowIndex++;
        }
        
        // 在表格后添加间距
        XWPFParagraph spaceParagraph = document.createParagraph();
        spaceParagraph.setSpacingAfter(200);
    }

    /**
     * 添加代码块（与模板样式统一）
     */
    private void addCodeBlock(XWPFDocument document, Element element) {
        // 获取代码内容，pre 标签通常包含 code 标签
        String codeText = element.text();
        
        // 将代码按行分割
        String[] lines = codeText.split("\n");
        
        for (String line : lines) {
            XWPFParagraph paragraph = document.createParagraph();

            org.openxmlformats.schemas.wordprocessingml.x2006.main.CTPPr pPr = paragraph.getCTP().getPPr();
            if (pPr == null) {
                pPr = paragraph.getCTP().addNewPPr();
            }
            org.openxmlformats.schemas.wordprocessingml.x2006.main.CTShd shd = pPr.addNewShd();
            shd.setFill("F5F5F5");

            paragraph.setIndentationLeft(240);
            paragraph.setIndentationRight(240);
            paragraph.setSpacingBefore(0);
            paragraph.setSpacingAfter(0);
            paragraph.setSpacingBetween(1.0, org.apache.poi.xwpf.usermodel.LineSpacingRule.AUTO);

            XWPFRun run = paragraph.createRun();
            run.setText(line.isEmpty() ? " " : line);
            setChineseFont(run, "Courier New", 10);
            run.setColor("333333");
        }
        
        // 添加代码块后的间距
        XWPFParagraph spaceParagraph = document.createParagraph();
        spaceParagraph.setSpacingAfter(120);
    }

    /**
     * 添加引用块（与模板样式统一）
     */
    private void addBlockquote(XWPFDocument document, Element element) {
        // 处理引用块中的内容，可能包含多个段落
        for (org.jsoup.nodes.Node child : element.childNodes()) {
            if (child instanceof Element childElem) {
                String tagName = childElem.tagName().toLowerCase();
                if ("p".equals(tagName)) {
                    // 每个 p 标签创建一个新的段落
                    XWPFParagraph paragraph = document.createParagraph();
                    paragraph.setIndentationLeft(480);
                    paragraph.setBorderLeft(org.apache.poi.xwpf.usermodel.Borders.SINGLE);
                    paragraph.setSpacingBetween(1.15, org.apache.poi.xwpf.usermodel.LineSpacingRule.AUTO);
                    paragraph.setSpacingAfter(80);
                    
                    // 处理段落中的格式化内容（包括 br、a 等）
                    // 直接遍历 p 标签的所有子节点，确保 br 和 a 等标签被正确处理
                    for (org.jsoup.nodes.Node pChild : childElem.childNodes()) {
                        processQuoteNode(paragraph, pChild);
                    }
                } else {
                    // 其他元素（如直接文本节点），创建一个段落
                    XWPFParagraph paragraph = document.createParagraph();
                    paragraph.setIndentationLeft(480);
                    paragraph.setBorderLeft(org.apache.poi.xwpf.usermodel.Borders.SINGLE);
                    paragraph.setSpacingBetween(1.15, org.apache.poi.xwpf.usermodel.LineSpacingRule.AUTO);
                    paragraph.setSpacingAfter(80);
                    
                    processQuoteNode(paragraph, child);
                }
            } else if (child instanceof org.jsoup.nodes.TextNode textNode) {
                // 如果是纯文本节点，创建段落
                String text = textNode.text().trim();
                if (!text.isEmpty()) {
                    XWPFParagraph paragraph = document.createParagraph();
                    paragraph.setIndentationLeft(480);
                    paragraph.setBorderLeft(org.apache.poi.xwpf.usermodel.Borders.SINGLE);
                    paragraph.setSpacingBetween(1.15, org.apache.poi.xwpf.usermodel.LineSpacingRule.AUTO);
                    paragraph.setSpacingAfter(80);
                    
                    XWPFRun run = paragraph.createRun();
                    run.setText(text);
                    setChineseFont(run, "Microsoft YaHei", 12);
                    run.setColor("666666");
                    run.setItalic(true);
                }
            }
        }
        
        // 引用块后添加间距
        XWPFParagraph spaceParagraph = document.createParagraph();
        spaceParagraph.setSpacingAfter(120);
    }

    /**
     * 处理引用块节点（支持格式化、链接、换行等）
     */
    private void processQuoteNode(XWPFParagraph paragraph, org.jsoup.nodes.Node node) {
        if (node instanceof org.jsoup.nodes.TextNode textNode) {
            String text = textNode.text();
            if (!text.isEmpty()) {
                XWPFRun run = paragraph.createRun();
                run.setText(text);
                setChineseFont(run, "Microsoft YaHei", 12);
                run.setColor("666666");
                run.setItalic(true);
            }
        } else if (node instanceof Element elem) {
            String tagName = elem.tagName().toLowerCase();
            
            // 特殊处理某些标签
            switch (tagName) {
                case "br" -> {
                    // 换行
                    paragraph.createRun().addBreak();
                }
                case "a" -> {
                    // 处理超链接
                    String linkText = elem.text();
                    String href = elem.attr("href");
                    
                    if (!href.isEmpty()) {
                        try {
                            // 创建超链接
                            String rId = createHyperlink(paragraph, href);
                            
                            // 创建超链接的 run
                            XWPFRun linkRun = paragraph.createRun();
                            linkRun.setText(linkText);
                            setChineseFont(linkRun, "Microsoft YaHei", 12);
                            linkRun.setColor("0563C1");  // 蓝色
                            linkRun.setUnderline(org.apache.poi.xwpf.usermodel.UnderlinePatterns.SINGLE);
                            linkRun.setItalic(true); // 引用块中的链接也保持斜体
                            
                            // 将 run 标记为超链接
                            org.openxmlformats.schemas.wordprocessingml.x2006.main.CTHyperlink ctHyperlink = 
                                paragraph.getCTP().addNewHyperlink();
                            ctHyperlink.setId(rId);
                            ctHyperlink.addNewR();
                            
                            // 将现有 run 的内容复制到超链接中
                            org.openxmlformats.schemas.wordprocessingml.x2006.main.CTR ctr = linkRun.getCTR();
                            ctHyperlink.setRArray(new org.openxmlformats.schemas.wordprocessingml.x2006.main.CTR[]{ctr});
                            
                            // 从段落中移除原 run（因为已经添加到超链接中了）
                            paragraph.getCTP().removeR(paragraph.getCTP().sizeOfRArray() - 1);
                        } catch (Exception e) {
                            log.warn("引用块中创建超链接失败: {}", e.getMessage());
                            // 如果失败，作为普通文本处理
                            XWPFRun textRun = paragraph.createRun();
                            textRun.setText(linkText);
                            setChineseFont(textRun, "Microsoft YaHei", 12);
                            textRun.setColor("666666");
                            textRun.setItalic(true);
                        }
                    } else {
                        // 如果没有 href，就作为普通文本
                        XWPFRun textRun = paragraph.createRun();
                        textRun.setText(linkText);
                        setChineseFont(textRun, "Microsoft YaHei", 12);
                        textRun.setColor("666666");
                        textRun.setItalic(true);
                    }
                }
                case "strong", "b" -> {
                    // 粗体
                    for (org.jsoup.nodes.Node child : elem.childNodes()) {
                        processQuoteNode(paragraph, child);
                    }
                    // 注意：这里需要设置粗体，但由于 processQuoteNode 的递归特性，
                    // 我们可以在处理文本节点时检查是否在 strong 标签内
                    // 为了简化，这里先递归处理，粗体效果需要额外处理
                }
                case "em", "i" -> {
                    // 斜体（引用块默认已经是斜体，这里保持）
                    for (org.jsoup.nodes.Node child : elem.childNodes()) {
                        processQuoteNode(paragraph, child);
                    }
                }
                default -> {
                    // 递归处理其他子节点
                    for (org.jsoup.nodes.Node child : elem.childNodes()) {
                        processQuoteNode(paragraph, child);
                    }
                }
            }
        }
    }

    /**
     * 添加水平分割线（优雅简洁的设计）
     */
    private void addHorizontalRule(XWPFDocument document) {
        XWPFParagraph paragraph = document.createParagraph();
        
        // 设置段落居中对齐
        paragraph.setAlignment(org.apache.poi.xwpf.usermodel.ParagraphAlignment.CENTER);
        
        // 设置上下间距
        paragraph.setSpacingBefore(300);
        paragraph.setSpacingAfter(300);

        // 获取段落属性
        org.openxmlformats.schemas.wordprocessingml.x2006.main.CTPPr pPr = paragraph.getCTP().getPPr();
        if (pPr == null) {
            pPr = paragraph.getCTP().addNewPPr();
        }

        // 设置段落边框
        org.openxmlformats.schemas.wordprocessingml.x2006.main.CTPBdr pBdr = pPr.addNewPBdr();
        org.openxmlformats.schemas.wordprocessingml.x2006.main.CTBorder bottomBorder = pBdr.addNewBottom();
        
        // 使用细线样式
        bottomBorder.setVal(org.openxmlformats.schemas.wordprocessingml.x2006.main.STBorder.SINGLE);
        
        // 设置边框宽度：6pt（1/8 英寸 = 细线）
        bottomBorder.setSz(java.math.BigInteger.valueOf(6));
        
        // 设置边框颜色为浅灰色
        bottomBorder.setColor("CCCCCC");
        
        // 设置边框与段落的距离
        bottomBorder.setSpace(java.math.BigInteger.valueOf(1));
        
        // 创建一个空的 run，但不添加任何文本
        // 这样边框会显示为一条分割线而不是包围一个长方形
    }

    /**
     * 设置表格不跨页分割
     */
    private void setTableKeepTogether(org.apache.poi.xwpf.usermodel.XWPFTable table) {
        try {
            org.openxmlformats.schemas.wordprocessingml.x2006.main.CTTbl ctTbl = table.getCTTbl();
            org.openxmlformats.schemas.wordprocessingml.x2006.main.CTTblPr tblPr = ctTbl.getTblPr();
            if (tblPr == null) {
                tblPr = ctTbl.addNewTblPr();
            }
            
            // 设置表格布局为固定布局（有助于防止分页）
            org.openxmlformats.schemas.wordprocessingml.x2006.main.CTTblLayoutType tblLayout = tblPr.addNewTblLayout();
            tblLayout.setType(org.openxmlformats.schemas.wordprocessingml.x2006.main.STTblLayoutType.FIXED);
            
            log.debug("表格设置为不跨页分割");
        } catch (Exception e) {
            log.warn("设置表格不分页失败: {}", e.getMessage());
        }
    }

    /**
     * 设置表格行不能跨页断开
     */
    private void setRowKeepTogether(org.apache.poi.xwpf.usermodel.XWPFTableRow row) {
        try {
            org.openxmlformats.schemas.wordprocessingml.x2006.main.CTRow ctRow = row.getCtRow();
            org.openxmlformats.schemas.wordprocessingml.x2006.main.CTTrPr trPr = ctRow.getTrPr();
            if (trPr == null) {
                trPr = ctRow.addNewTrPr();
            }
            
            // 设置行不能跨页断开（CantSplit）
            // 这样确保每一行的内容不会在页面中间被断开
            org.openxmlformats.schemas.wordprocessingml.x2006.main.CTOnOff cantSplit = trPr.addNewCantSplit();
            cantSplit.setVal(true);
            
        } catch (Exception e) {
            log.warn("设置行不分页失败: {}", e.getMessage());
        }
    }

    /**
     * 添加图片到段落（支持 base64 和 HTTP URL）
     */
    private void addImageToParagraph(XWPFParagraph paragraph, String src, String alt) throws Exception {
        addImageToParagraph(paragraph, src, alt, true);
    }

    /**
     * 添加图片到段落（支持 base64 和 HTTP URL）
     * @param paragraph 段落
     * @param src 图片源
     * @param alt 替代文本
     * @param center 是否居中
     */
    private void addImageToParagraph(XWPFParagraph paragraph, String src, String alt, boolean center) throws Exception {
        byte[] imageBytes;
        int pictureType;
        String fileName = alt != null && !alt.isEmpty() ? alt : "image";
        
        if (src.startsWith("data:image")) {
            // 处理 base64 图片
            String base64Data = src.substring(src.indexOf(",") + 1);
            imageBytes = java.util.Base64.getDecoder().decode(base64Data);
            
            // 根据 MIME 类型判断图片类型
            if (src.contains("image/jpeg") || src.contains("image/jpg")) {
                pictureType = org.apache.poi.xwpf.usermodel.XWPFDocument.PICTURE_TYPE_JPEG;
                fileName += ".jpg";
            } else if (src.contains("image/png")) {
                pictureType = org.apache.poi.xwpf.usermodel.XWPFDocument.PICTURE_TYPE_PNG;
                fileName += ".png";
            } else if (src.contains("image/gif")) {
                pictureType = org.apache.poi.xwpf.usermodel.XWPFDocument.PICTURE_TYPE_GIF;
                fileName += ".gif";
                // 注意：Word 支持 GIF 格式，但 GIF 动画在 Word 中只会显示第一帧，不会播放动画
                // 这是 Word 文档格式的限制，无法通过代码解决
                log.debug("检测到 GIF 图片，注意：Word 中 GIF 动画不会播放，只显示第一帧");
            } else {
                pictureType = org.apache.poi.xwpf.usermodel.XWPFDocument.PICTURE_TYPE_PNG;
                fileName += ".png";
            }
        } else if (src.startsWith("http://") || src.startsWith("https://")) {
            // 处理 HTTP URL 图片
            log.info("下载图片: {}", src);
            imageBytes = downloadImage(src);
            
            // 根据 URL 扩展名判断图片类型，如果是 WebP 则跳过（native 库不可用）
            String lowerSrc = src.toLowerCase();
            if (lowerSrc.endsWith(".webp") || lowerSrc.contains(".webp?") || isWebPFormat(imageBytes)) {
                // WebP 图片：native 库不可用，无法转换，跳过此图片
                log.warn("检测到 WebP 图片但 native 库不可用，跳过图片: {}", src);
                return; // 直接返回，不添加图片到 Word
            } else if (lowerSrc.endsWith(".jpg") || lowerSrc.endsWith(".jpeg")) {
                pictureType = org.apache.poi.xwpf.usermodel.XWPFDocument.PICTURE_TYPE_JPEG;
                fileName += ".jpg";
            } else if (lowerSrc.endsWith(".png")) {
                pictureType = org.apache.poi.xwpf.usermodel.XWPFDocument.PICTURE_TYPE_PNG;
                fileName += ".png";
            } else if (lowerSrc.endsWith(".gif")) {
                pictureType = org.apache.poi.xwpf.usermodel.XWPFDocument.PICTURE_TYPE_GIF;
                fileName += ".gif";
                // 注意：Word 支持 GIF 格式，但 GIF 动画在 Word 中只会显示第一帧，不会播放动画
                // 这是 Word 文档格式的限制，无法通过代码解决
                log.debug("检测到 GIF 图片，注意：Word 中 GIF 动画不会播放，只显示第一帧");
            } else {
                // 默认按 JPEG 处理
                pictureType = org.apache.poi.xwpf.usermodel.XWPFDocument.PICTURE_TYPE_JPEG;
                fileName += ".jpg";
            }
        } else {
            log.warn("不支持的图片类型: {}", src);
            return;
        }
        
        // 根据参数决定是否设置段落居中对齐
        if (center) {
            paragraph.setAlignment(org.apache.poi.xwpf.usermodel.ParagraphAlignment.CENTER);
        }
        
        // 插入图片到 Word
        XWPFRun imgRun = paragraph.createRun();
        
        // 获取图片实际尺寸
        java.awt.Dimension imageDimension = getImageDimension(imageBytes);
        
        // 计算图片大小（确保图片宽度 ≤ 文字段落宽度，不超过页面）
        // Word A4 页面可用宽度约 16cm（6.3英寸），按 96 DPI 计算 ≈ 605 像素
        // 图片最大宽度：605 × 0.9 = 545 像素（90% 页面宽度）
        int maxImageWidthPixels = 545;  // 图片最大宽度（90% 页面宽度，像素）
        
        int finalWidth;
        int finalHeight;
        
        if (imageDimension != null) {
            // 根据图片实际宽高比例计算
            double aspectRatio = (double) imageDimension.height / imageDimension.width;
            
            // 计算目标宽度（像素）
            int targetWidthPixels = Math.min(imageDimension.width, maxImageWidthPixels);
            // 图片较小，保持原始尺寸（不放大）
            // 图片较大，缩放到最大宽度

            int targetHeightPixels = (int) (targetWidthPixels * aspectRatio);
            
            // 将像素转换为 EMU
            // 1 像素 ≈ 9525 EMU（基于 96 DPI：1 inch = 914400 EMU, 1 inch = 96 pixels）
            // 更准确的转换：像素 × 914400 / 96 = 像素 × 9525
            finalWidth = targetWidthPixels * 9525;
            finalHeight = targetHeightPixels * 9525;
            
            log.info("图片原始尺寸: {}x{} 像素, Word显示: {}x{} 像素（宽度 ≤ 页面宽度）", 
                imageDimension.width, imageDimension.height,
                targetWidthPixels, targetHeightPixels);
        } else {
            // 如果无法获取尺寸，使用默认 90% 宽度，比例 4:3
            int defaultHeightPixels = (int) (maxImageWidthPixels * 0.75);
            finalWidth = maxImageWidthPixels * 9525;
            finalHeight = defaultHeightPixels * 9525;
            
            log.info("使用默认图片尺寸: {}x{} 像素（90% 宽度，居中）",
                    maxImageWidthPixels, defaultHeightPixels);
        }
        
        imgRun.addPicture(
            new java.io.ByteArrayInputStream(imageBytes),
            pictureType,
            fileName,
            finalWidth,
            finalHeight
        );
        
        log.info("成功添加图片: {} ({} bytes)", fileName, imageBytes.length);
    }

    /**
     * 添加 Emoji 图片到段落（特殊处理：1em 大小，内联显示）
     * 参考 CSS: img.is-emoji { width: 1em; height: 1em; vertical-align: baseline; display: inline-block; }
     */
    private void addEmojiImageToParagraph(XWPFParagraph paragraph, String src, String alt) throws Exception {
        byte[] imageBytes;
        int pictureType;
        String fileName = alt != null && !alt.isEmpty() ? alt : "emoji";
        
        if (src.startsWith("data:image")) {
            // 处理 base64 图片
            String base64Data = src.substring(src.indexOf(",") + 1);
            imageBytes = java.util.Base64.getDecoder().decode(base64Data);
            
            // 根据 MIME 类型判断图片类型
            if (src.contains("image/jpeg") || src.contains("image/jpg")) {
                pictureType = org.apache.poi.xwpf.usermodel.XWPFDocument.PICTURE_TYPE_JPEG;
                fileName += ".jpg";
            } else if (src.contains("image/png")) {
                pictureType = org.apache.poi.xwpf.usermodel.XWPFDocument.PICTURE_TYPE_PNG;
                fileName += ".png";
            } else if (src.contains("image/gif")) {
                pictureType = org.apache.poi.xwpf.usermodel.XWPFDocument.PICTURE_TYPE_GIF;
                fileName += ".gif";
            } else {
                pictureType = org.apache.poi.xwpf.usermodel.XWPFDocument.PICTURE_TYPE_PNG;
                fileName += ".png";
            }
        } else if (src.startsWith("http://") || src.startsWith("https://")) {
            // 处理 HTTP URL 图片
            log.debug("下载 Emoji 图片: {}", src);
            imageBytes = downloadImage(src);
            
            // 根据 URL 扩展名判断图片类型
            String lowerSrc = src.toLowerCase();
            if (lowerSrc.endsWith(".webp") || lowerSrc.contains(".webp?") || isWebPFormat(imageBytes)) {
                log.warn("检测到 WebP Emoji 图片但 native 库不可用，跳过图片: {}", src);
                return;
            } else if (lowerSrc.endsWith(".jpg") || lowerSrc.endsWith(".jpeg")) {
                pictureType = org.apache.poi.xwpf.usermodel.XWPFDocument.PICTURE_TYPE_JPEG;
                fileName += ".jpg";
            } else if (lowerSrc.endsWith(".png")) {
                pictureType = org.apache.poi.xwpf.usermodel.XWPFDocument.PICTURE_TYPE_PNG;
                fileName += ".png";
            } else if (lowerSrc.endsWith(".gif")) {
                pictureType = org.apache.poi.xwpf.usermodel.XWPFDocument.PICTURE_TYPE_GIF;
                fileName += ".gif";
            } else {
                pictureType = org.apache.poi.xwpf.usermodel.XWPFDocument.PICTURE_TYPE_PNG;
                fileName += ".png";
            }
        } else {
            log.warn("不支持的 Emoji 图片类型: {}", src);
            return;
        }
        
        // Emoji 图片不居中，内联显示（类似文字）
        // 不设置段落对齐方式，保持默认左对齐
        
        // 获取段落字体大小（用于计算 1em）
        int fontSize = 12; // 默认字体大小
        // 尝试从段落中获取字体大小
        if (!paragraph.getRuns().isEmpty()) {
            XWPFRun firstRun = paragraph.getRuns().get(0);
            try {
                int runFontSize = firstRun.getFontSize();
                if (runFontSize > 0) {
                    fontSize = runFontSize;
                }
            } catch (Exception e) {
                // 如果获取失败，使用默认值
                log.debug("无法获取字体大小，使用默认值 12");
            }
        }
        
        // 计算 1em 的大小（像素转 EMU）
        // 1em = 字体大小（磅），1 磅 = 1/72 英寸，1 英寸 = 914400 EMU
        // 所以 1em = fontSize * 914400 / 72 = fontSize * 12700 EMU
        int emSize = fontSize * 12700; // 1em 对应的 EMU 值
        
        // 计算图片尺寸（1em x 1em）
        // 保持图片原始宽高比，但限制最大尺寸为 1em
        java.awt.Dimension imageDimension = getImageDimension(imageBytes);
        
        int finalWidth;
        int finalHeight;
        
        if (imageDimension != null) {
            // 计算缩放比例，确保图片适应 1em 尺寸
            double aspectRatio = (double) imageDimension.height / imageDimension.width;
            
            // 以较小的维度为准（宽度或高度），确保图片完全适应 1em 正方形
            if (aspectRatio > 1.0) {
                // 图片更高，以高度为准
                finalHeight = emSize;
                finalWidth = (int) (emSize / aspectRatio);
            } else {
                // 图片更宽或正方形，以宽度为准
                finalWidth = emSize;
                finalHeight = (int) (emSize * aspectRatio);
            }
        } else {
            // 如果无法获取尺寸，使用 1em x 1em
            finalWidth = emSize;
            finalHeight = emSize;
        }
        
        // 插入图片到 Word（内联显示）
        XWPFRun imgRun = paragraph.createRun();
        
        imgRun.addPicture(
            new java.io.ByteArrayInputStream(imageBytes),
            pictureType,
            fileName,
            finalWidth,
            finalHeight
        );
        
        // 设置垂直对齐为 baseline（通过设置图片的行内对齐方式）
        // 在 Word 中，图片默认就是 baseline 对齐的
        
        log.debug("成功添加 Emoji 图片: {} ({} bytes, 尺寸: {}x{} EMU, 1em={} EMU)", 
            fileName, imageBytes.length, finalWidth, finalHeight, emSize);
    }

    /**
     * 添加图片到表格单元格（适应单元格大小）
     */
    private void addImageToTableCell(XWPFParagraph paragraph, String src, String alt, 
                                     org.apache.poi.xwpf.usermodel.XWPFTableCell tableCell) throws Exception {
        byte[] imageBytes;
        int pictureType;
        String fileName = alt != null && !alt.isEmpty() ? alt : "image";
        
        if (src.startsWith("data:image")) {
            // 处理 base64 图片
            String base64Data = src.substring(src.indexOf(",") + 1);
            imageBytes = java.util.Base64.getDecoder().decode(base64Data);
            
            // 根据 MIME 类型判断图片类型
            if (src.contains("image/jpeg") || src.contains("image/jpg")) {
                pictureType = org.apache.poi.xwpf.usermodel.XWPFDocument.PICTURE_TYPE_JPEG;
                fileName += ".jpg";
            } else if (src.contains("image/png")) {
                pictureType = org.apache.poi.xwpf.usermodel.XWPFDocument.PICTURE_TYPE_PNG;
                fileName += ".png";
            } else if (src.contains("image/gif")) {
                pictureType = org.apache.poi.xwpf.usermodel.XWPFDocument.PICTURE_TYPE_GIF;
                fileName += ".gif";
            } else {
                pictureType = org.apache.poi.xwpf.usermodel.XWPFDocument.PICTURE_TYPE_PNG;
                fileName += ".png";
            }
        } else if (src.startsWith("http://") || src.startsWith("https://")) {
            // 处理 HTTP URL 图片
            log.info("下载图片: {}", src);
            imageBytes = downloadImage(src);
            
            // 根据 URL 扩展名判断图片类型
            String lowerSrc = src.toLowerCase();
            if (lowerSrc.endsWith(".webp") || lowerSrc.contains(".webp?") || isWebPFormat(imageBytes)) {
                log.warn("检测到 WebP 图片但 native 库不可用，跳过图片: {}", src);
                return;
            } else if (lowerSrc.endsWith(".jpg") || lowerSrc.endsWith(".jpeg")) {
                pictureType = org.apache.poi.xwpf.usermodel.XWPFDocument.PICTURE_TYPE_JPEG;
                fileName += ".jpg";
            } else if (lowerSrc.endsWith(".png")) {
                pictureType = org.apache.poi.xwpf.usermodel.XWPFDocument.PICTURE_TYPE_PNG;
                fileName += ".png";
            } else if (lowerSrc.endsWith(".gif")) {
                pictureType = org.apache.poi.xwpf.usermodel.XWPFDocument.PICTURE_TYPE_GIF;
                fileName += ".gif";
                // 注意：Word 支持 GIF 格式，但 GIF 动画在 Word 中只会显示第一帧，不会播放动画
                // 这是 Word 文档格式的限制，无法通过代码解决
                log.debug("检测到 GIF 图片，注意：Word 中 GIF 动画不会播放，只显示第一帧");
            } else {
                pictureType = org.apache.poi.xwpf.usermodel.XWPFDocument.PICTURE_TYPE_JPEG;
                fileName += ".jpg";
            }
        } else {
            log.warn("不支持的图片类型: {}", src);
            return;
        }
        
        // 计算单元格可用尺寸
        // 获取单元格宽度和高度（减去内边距）
        // DXA 单位：1 DXA = 1/20 磅 = 1/1440 英寸
        // 1 英寸 = 914400 EMU，所以 1 DXA = 914400/1440 = 635 EMU
        
        // 获取表格列数，用于估算单元格宽度
        org.apache.poi.xwpf.usermodel.XWPFTableRow tableRow = tableCell.getTableRow();
        int colCount = tableRow != null ? tableRow.getTableCells().size() : 4;
        
        // A4 页面可用宽度约 10000 DXA（减去左右边距后）
        // 平均分配给各列
        long cellWidthDXA = 10000L / colCount;
        
        // 获取行高（Twips 单位，1 Twips = 1/20 磅）
        long cellHeightDXA = 500; // 默认行高 500 Twips = 25 DXA（这里直接使用 DXA）
        if (tableRow != null) {
            long rowHeightTwips = tableRow.getHeight();
            if (rowHeightTwips > 0) {
                cellHeightDXA = rowHeightTwips / 20; // Twips 转 DXA
            }
        }
        
        // 减去内边距：左右各 180 DXA，上下各 200 DXA
        long availableWidthDXA = Math.max(500, cellWidthDXA - 360); // 至少保留 500 DXA
        long availableHeightDXA = Math.max(300, cellHeightDXA - 400); // 至少保留 300 DXA
        
        // 转换为 EMU（1 DXA = 635 EMU，更准确的是 914400/1440 = 635）
        // 但 Apache POI 使用的是 1 DXA = 635 EMU 的近似值
        long availableWidthEMU = availableWidthDXA * 635;
        long availableHeightEMU = availableHeightDXA * 635;
        
        // 获取图片实际尺寸
        java.awt.Dimension imageDimension = getImageDimension(imageBytes);
        
        int finalWidth;
        int finalHeight;
        
        if (imageDimension != null) {
            // 计算图片宽高比
            double imageAspectRatio = (double) imageDimension.height / imageDimension.width;
            double cellAspectRatio = (double) availableHeightEMU / availableWidthEMU;
            
            // 根据单元格和图片的比例，选择适应方式
            if (imageAspectRatio > cellAspectRatio) {
                // 图片更高，以高度为准
                finalHeight = (int) availableHeightEMU;
                finalWidth = (int) (availableHeightEMU / imageAspectRatio);
            } else {
                // 图片更宽，以宽度为准
                finalWidth = (int) availableWidthEMU;
                finalHeight = (int) (availableWidthEMU * imageAspectRatio);
            }
            
            log.info("表格单元格图片: 单元格可用 {}x{} DXA ({}x{} EMU), 图片原始 {}x{} 像素, 适配后 {}x{} EMU", 
                availableWidthDXA, availableHeightDXA,
                availableWidthEMU, availableHeightEMU,
                imageDimension.width, imageDimension.height,
                finalWidth, finalHeight);
        } else {
            // 如果无法获取尺寸，使用单元格的 80% 作为默认大小（保持比例）
            finalWidth = (int) (availableWidthEMU * 0.8);
            finalHeight = (int) (availableHeightEMU * 0.8);
            
            log.info("表格单元格图片: 使用默认尺寸 {}x{} EMU (单元格的 80%)", finalWidth, finalHeight);
        }
        
        // 插入图片到 Word（不居中）
        XWPFRun imgRun = paragraph.createRun();
        
        imgRun.addPicture(
            new java.io.ByteArrayInputStream(imageBytes),
            pictureType,
            fileName,
            finalWidth,
            finalHeight
        );
        
        log.info("成功添加表格单元格图片: {} ({} bytes, 尺寸: {}x{} EMU)", 
            fileName, imageBytes.length, finalWidth, finalHeight);
    }

    /**
     * 获取图片尺寸
     * 注意：如果图片是 WebP 格式，直接返回 null（避免触发 UnsatisfiedLinkError）
     * 因为 native 库在当前环境下不可用
     */
    private java.awt.Dimension getImageDimension(byte[] imageBytes) {
        // 先检查是否是 WebP 格式（避免 ImageIO 自动加载 WebP 解码器导致 UnsatisfiedLinkError）
        if (isWebPFormat(imageBytes)) {
            log.debug("检测到 WebP 格式，跳过尺寸读取（native 库不可用）");
            return null; // 直接返回 null，避免触发 UnsatisfiedLinkError
        }
        
        // 非 WebP 格式，正常读取
        try {
            java.io.ByteArrayInputStream bais = new java.io.ByteArrayInputStream(imageBytes);
            java.awt.image.BufferedImage image = javax.imageio.ImageIO.read(bais);
            if (image != null) {
                return new java.awt.Dimension(image.getWidth(), image.getHeight());
            }
        } catch (Exception e) {
            log.warn("获取图片尺寸失败: {}", e.getMessage());
        } catch (Error e) {
            // 捕获可能的 Error（虽然理论上不会发生，因为已经排除了 WebP）
            log.warn("获取图片尺寸失败（Error）: {}", e.getMessage());
            // 不重新抛出 Error，直接返回 null
        }
        return null;
    }
    
    /**
     * 检查字节数组是否是 WebP 格式（通过文件头判断）
     * WebP 文件头: RIFF (4 bytes) + file size (4 bytes) + WEBP (4 bytes)
     */
    private boolean isWebPFormat(byte[] bytes) {
        if (bytes == null || bytes.length < 12) {
            return false;
        }
        // 检查 RIFF 头
        if (bytes[0] != 'R' || bytes[1] != 'I' || bytes[2] != 'F' || bytes[3] != 'F') {
            return false;
        }
        // 检查 WEBP 标识
        if (bytes[8] != 'W' || bytes[9] != 'E' || bytes[10] != 'B' || bytes[11] != 'P') {
            return false;
        }
        return true;
    }
    

    /**
     * 下载网络图片
     */
    protected byte[] downloadImage(String imageUrl) throws Exception {
        try {
            HttpURLConnection connection = getHttpURLConnection(imageUrl);

            int responseCode = connection.getResponseCode();
            if (responseCode == java.net.HttpURLConnection.HTTP_OK) {
                try (java.io.InputStream inputStream = connection.getInputStream();
                     java.io.ByteArrayOutputStream outputStream = new java.io.ByteArrayOutputStream()) {
                    
                    byte[] buffer = new byte[4096];
                    int bytesRead;
                    while ((bytesRead = inputStream.read(buffer)) != -1) {
                        outputStream.write(buffer, 0, bytesRead);
                    }
                    
                    return outputStream.toByteArray();
                }
            } else {
                throw new Exception("HTTP 错误码: " + responseCode);
            }
        } catch (Exception e) {
            log.error("下载图片失败 [{}]: {}", imageUrl, e.getMessage());
            throw e;
        }
    }

    private static @NotNull HttpURLConnection getHttpURLConnection(String imageUrl) throws IOException {
        java.net.URL url = new java.net.URL(imageUrl);
        HttpURLConnection connection = (HttpURLConnection) url.openConnection();

        // 设置请求头（模拟浏览器）
        connection.setRequestMethod("GET");
        connection.setConnectTimeout(10000);
        connection.setReadTimeout(10000);
        connection.setRequestProperty("User-Agent",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36");
        return connection;
    }

    /**
     * 从 HTML 中提取水印文本并移除该元素
     */
    private String extractWatermarkText(Element body) {
        try {
            // 查找 class 为 footer-credit 的元素
            Elements footerElements = body.select(".footer-credit");
            if (!footerElements.isEmpty()) {
                Element footerElement = footerElements.first();
                assert footerElement != null;
                String text = footerElement.text().trim();
                
                // 移除该元素，避免重复显示
                footerElement.remove();
                
                log.info("检测到水印文本: {}", text);
                return text;
            }
            
            // 也可以查找包含特定文本的元素
            Elements allElements = body.select("*");
            for (Element elem : allElements) {
                String text = elem.ownText();
                if (text.contains("由联想百应") || text.contains("智能体AI生成")) {
                    elem.remove();
                    log.info("检测到水印文本: {}", text);
                    return text.trim();
                }
            }
        } catch (Exception e) {
            log.warn("提取水印文本失败: {}", e.getMessage());
        }
        return null;
    }

    /**
     * 在 Word 文档中添加水印（作为页脚样式）
     */
    private void addWatermark(XWPFDocument document, String watermarkText) {
        try {
            // 在文档末尾添加分隔线
            XWPFParagraph separatorParagraph = document.createParagraph();
            separatorParagraph.setAlignment(org.apache.poi.xwpf.usermodel.ParagraphAlignment.CENTER);
            separatorParagraph.setSpacingBefore(400);
            separatorParagraph.setSpacingAfter(200);
            
            // 添加分隔线
            org.openxmlformats.schemas.wordprocessingml.x2006.main.CTPPr sepPPr = separatorParagraph.getCTP().getPPr();
            if (sepPPr == null) {
                sepPPr = separatorParagraph.getCTP().addNewPPr();
            }
            org.openxmlformats.schemas.wordprocessingml.x2006.main.CTPBdr sepPBdr = sepPPr.addNewPBdr();
            org.openxmlformats.schemas.wordprocessingml.x2006.main.CTBorder sepBorder = sepPBdr.addNewTop();
            sepBorder.setVal(org.openxmlformats.schemas.wordprocessingml.x2006.main.STBorder.SINGLE);
            sepBorder.setSz(java.math.BigInteger.valueOf(4));
            sepBorder.setColor("E0E0E0");
            
            // 创建水印段落
            XWPFParagraph watermarkParagraph = document.createParagraph();
            watermarkParagraph.setAlignment(org.apache.poi.xwpf.usermodel.ParagraphAlignment.CENTER);
            watermarkParagraph.setSpacingAfter(200);
            
            // 创建水印文本
            XWPFRun watermarkRun = watermarkParagraph.createRun();
            watermarkRun.setText(watermarkText);
            setChineseFont(watermarkRun, "Microsoft YaHei", 9);
            watermarkRun.setColor("B0B0B0");  // 中灰色
            watermarkRun.setItalic(true);     // 斜体
            
            log.info("成功添加水印: {}", watermarkText);
            
        } catch (Exception e) {
            log.error("添加水印失败: {}", e.getMessage(), e);
        }
    }
}

