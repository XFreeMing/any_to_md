package com.baiying.ai.mcpplatformapi.md_to_any.service;

import java.io.BufferedReader;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.StringReader;
import java.net.HttpURLConnection;
import java.net.URI;
import java.net.URISyntaxException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Arrays;
import java.util.Base64;
import java.util.Comparator;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import org.apache.batik.transcoder.SVGAbstractTranscoder;
import org.apache.batik.transcoder.TranscoderInput;
import org.apache.batik.transcoder.TranscoderOutput;
import org.apache.batik.transcoder.image.PNGTranscoder;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.core.io.ClassPathResource;

import com.baiying.ai.mcpplatformapi.common.client.attchment.AttachmentClient;
import com.baiying.ai.mcpplatformapi.md_to_any.MdToAnyIO;
import com.vladsch.flexmark.ext.anchorlink.AnchorLinkExtension;
import com.vladsch.flexmark.ext.autolink.AutolinkExtension;
import com.vladsch.flexmark.ext.emoji.EmojiExtension;
import com.vladsch.flexmark.ext.emoji.EmojiImageType;
import com.vladsch.flexmark.ext.gfm.strikethrough.StrikethroughExtension;
import com.vladsch.flexmark.ext.tables.TablesExtension;
import com.vladsch.flexmark.ext.toc.TocExtension;
import com.vladsch.flexmark.html.HtmlRenderer;
import com.vladsch.flexmark.parser.Parser;
import com.vladsch.flexmark.util.ast.Node;
import com.vladsch.flexmark.util.data.MutableDataSet;

import cn.hutool.core.io.resource.ResourceUtil;
import lombok.extern.slf4j.Slf4j;

/**
 * Markdown 转换服务抽象基类
 * 包含所有公共方法
 */
@Slf4j
public abstract class AbstractMarkdownToAnyService implements MarkdownToAnyService {

    protected final AttachmentClient attachmentClient;
    protected final EmojiConfigLoader emojiConfigLoader;
    protected final String templateUrl;

    public AbstractMarkdownToAnyService(
            AttachmentClient attachmentClient,
            EmojiConfigLoader emojiConfigLoader,
            @Value("${md_to_any.template_url:https://smbfiletest.oss-cn-beijing.aliyuncs.com/md_to_any_template/md_to_html.html}") String templateUrl) {
        this.attachmentClient = attachmentClient;
        this.emojiConfigLoader = emojiConfigLoader;
        this.templateUrl = templateUrl;
    }

    @Override
    public MdToAnyIO.Result convertMarkdown(String mdContent) throws Exception {
        String fileName = "markdown_" + System.currentTimeMillis();
        String aiCodingId = "ai_coding_" + System.currentTimeMillis();
        return convertMarkdown(aiCodingId, mdContent, fileName);
    }

    /**
     * 将 Markdown 内容转换为 HTML（带Emoji支持）
     */
    protected String convertMarkdownToHtmlWithEmoji(String markdown) {
        try {
            MutableDataSet options = new MutableDataSet();

            // 启用扩展功能（包括Emoji）
            options.set(Parser.EXTENSIONS, Arrays.asList(
                    TablesExtension.create(),
                    StrikethroughExtension.create(),
                    AutolinkExtension.create(),
                    AnchorLinkExtension.create(),
                    TocExtension.create(),
                    EmojiExtension.create()));

            // 配置Emoji扩展使用Unicode字符
            options.set(EmojiExtension.USE_IMAGE_TYPE, EmojiImageType.UNICODE_ONLY);

            // 表格渲染选项
            options.set(TablesExtension.COLUMN_SPANS, false)
                    .set(TablesExtension.APPEND_MISSING_COLUMNS, true)
                    .set(TablesExtension.DISCARD_EXTRA_COLUMNS, true)
                    .set(TablesExtension.HEADER_SEPARATOR_COLUMN_MATCH, true);

            Parser parser = Parser.builder(options).build();
            HtmlRenderer renderer = HtmlRenderer.builder(options).build();

            Node document = parser.parse(markdown);
            String htmlBody = renderer.render(document);

            return appendTemplateToHtml(htmlBody);

        } catch (Exception e) {
            log.error("Markdown 转 HTML 失败: {}", e.getMessage(), e);
            throw new RuntimeException("Markdown 转 HTML 失败", e);
        }
    }

    /**
     * 处理HTML中的Emoji，将Unicode emoji转换为img标签
     */
    protected String processEmojiInHtml(String html) {
        log.info("开始处理HTML中的Emoji，原始HTML长度: {}", html.length());

        String emojiRegex = emojiConfigLoader.buildEmojiRegex();
        log.info("使用的emoji正则表达式: {}", emojiRegex);

        StringBuilder result = new StringBuilder();
        Pattern pattern = Pattern.compile(emojiRegex);
        
        // 手动遍历字符串，处理代理对（因为Java正则表达式无法正确匹配代理对）
        // 使用 codePointAt() 和 charCount() 来正确处理代理对
        // 结合正则表达式和代码点检查来判断是否是emoji
        int i = 0;
        int emojiCount = 0;
        int successCount = 0;
        
        while (i < html.length()) {
            int codePoint = html.codePointAt(i);
            int charCount = Character.charCount(codePoint);
            
            // 构建完整的字符/代理对字符串
            String emojiCandidate = html.substring(i, i + charCount);
            
            // 使用代码点检查或正则表达式检查是否是emoji
            boolean isEmoji = false;
            
            // 方法1: 对于代理对emoji，使用代码点范围检查（更可靠）
            if (charCount == 2) {
                // 代理对，使用代码点检查
                isEmoji = isEmojiCodePoint(codePoint) || isTextSymbol(codePoint) || isArrowOrSymbol(codePoint);
            } else {
                // 单个字符，先尝试正则表达式，如果失败再使用代码点检查
                Matcher matcher = pattern.matcher(emojiCandidate);
                if (matcher.matches()) {
                    isEmoji = true;
                } else {
                    // 正则匹配失败，使用代码点检查作为后备
                    isEmoji = isEmojiCodePoint(codePoint) || isTextSymbol(codePoint) || isArrowOrSymbol(codePoint);
                }
            }
            
            if (isEmoji) {
                // 匹配成功，这是一个emoji
                emojiCount++;
                log.debug("找到emoji #{}: [{}] (Unicode: {}, 代码点: U+{})",
                        emojiCount, emojiCandidate, getEmojiUnicodeInfo(emojiCandidate),
                        Integer.toHexString(codePoint).toUpperCase());
                
                String replacement = convertEmojiToImg(emojiCandidate);
                if (replacement.contains("<img")) {
                    successCount++;
                    log.debug("成功转换emoji #{}: {} -> img标签", emojiCount, emojiCandidate);
                    result.append(replacement);
                } else {
                    log.debug("emoji #{}: {} 使用原始字符", emojiCount, emojiCandidate);
                    result.append(emojiCandidate);
                }
                
                i += charCount; // 跳过已处理的字符（代理对跳过2个char，单个字符跳过1个char）
            } else {
                // 不是emoji，按字符追加（保持原有文本结构）
                if (charCount == 2) {
                    // 代理对，但不是emoji，需要按char追加
                    result.append(html.charAt(i));
                    result.append(html.charAt(i + 1));
                } else {
                    // 单个字符
                    result.append(html.charAt(i));
                }
                i += charCount;
            }
        }

        String cleaned = removeResidualEmojiControlChars(result.toString());

        log.info("Emoji处理完成，共找到{}个emoji，成功转换{}个，处理后HTML长度: {}",
                emojiCount, successCount, cleaned.length());
        return cleaned;
    }

    /**
     * 去除残留的 Emoji 控制字符
     */
    protected String removeResidualEmojiControlChars(String text) {
        if (text == null || text.isEmpty()) {
            return text;
        }
        return text
                .replace("\uFE0F", "")
                .replace("\uFE0E", "")
                .replace("\u200D", "");
    }

    /**
     * 判断是否为emoji代码点（用于代码点检查，补充正则表达式的不足）
     */
    private boolean isEmojiCodePoint(int codePoint) {
        return (codePoint >= 0x1F600 && codePoint <= 0x1F64F) || // 表情符号
               (codePoint >= 0x1F300 && codePoint <= 0x1F5FF) || // 杂项符号
               (codePoint >= 0x1F680 && codePoint <= 0x1F6FF) || // 交通和地图符号
               (codePoint >= 0x1F1E6 && codePoint <= 0x1F1FF) || // 区域指示符号
               (codePoint >= 0x2600 && codePoint <= 0x26FF) ||   // 杂项符号
               (codePoint >= 0x2700 && codePoint <= 0x27BF) ||   // 装饰符号
               (codePoint >= 0x1F900 && codePoint <= 0x1F9FF) || // 补充符号和象形文字
               (codePoint >= 0x1FA70 && codePoint <= 0x1FAFF);   // 扩展A符号
    }

    /**
     * 判断是否为文本符号
     */
    private boolean isTextSymbol(int codePoint) {
        return (codePoint >= 0x0023 && codePoint <= 0x0039) || // #-9
               (codePoint >= 0x24C2 && codePoint <= 0x24C2) ||   // Ⓜ
               (codePoint >= 0x3297 && codePoint <= 0x3299) ||  // 日文符号
               (codePoint >= 0x3030 && codePoint <= 0x303D);   // 波浪线等符号
    }

    /**
     * 判断是否为箭头或其他符号
     */
    private boolean isArrowOrSymbol(int codePoint) {
        return (codePoint >= 0x2190 && codePoint <= 0x21FF) ||
               (codePoint >= 0x203C && codePoint <= 0x2049) ||
               (codePoint >= 0x2122 && codePoint <= 0x2139) ||
               (codePoint >= 0x231A && codePoint <= 0x23CF) ||
               (codePoint >= 0x23E9 && codePoint <= 0x23F3) ||
               (codePoint >= 0x23F8 && codePoint <= 0x23FA) ||
               (codePoint >= 0x2934 && codePoint <= 0x2935) ||
               (codePoint >= 0x2B05 && codePoint <= 0x2B07) ||
               (codePoint >= 0x2B1B && codePoint <= 0x2B1C) ||
               (codePoint >= 0x2B50 && codePoint <= 0x2B55);
    }


    /**
     * 获取emoji的Unicode信息（用于调试）
     */
    protected String getEmojiUnicodeInfo(String emoji) {
        StringBuilder info = new StringBuilder();
        for (int i = 0; i < emoji.length();) {
            int codePoint = emoji.codePointAt(i);
            if (!info.isEmpty())
                info.append("-");
            info.append("U+").append(Integer.toHexString(codePoint).toUpperCase());
            i += Character.charCount(codePoint);
        }
        return info.toString();
    }

    /**
     * 将单个emoji转换为img标签
     */
    protected String convertEmojiToImg(String emoji) {
        try {
            // 使用 EmojiConfigLoader 的智能映射功能获取SVG URL
            String svgUrl = emojiConfigLoader.getEmojiSvgUrl(emoji);
            
            // 从URL中提取代码点（去除 /static/twemoji/assets/svg/ 前缀和 .svg 后缀）
            String svgCodePoint = extractCodePointFromUrl(svgUrl);
            if (svgCodePoint == null) {
                log.info("无法从URL提取emoji代码点: {}, URL: {}", emoji, svgUrl);
                return convertEmojiToFallbackImg(emoji);
            }

            // 转换为classpath路径（去掉开头的 /）
            String classpathPath = svgUrl.startsWith("/") ? svgUrl.substring(1) : svgUrl;
            ClassPathResource svgResource = new ClassPathResource(classpathPath);

            if (svgResource.exists()) {
                try (InputStream svgStream = svgResource.getInputStream()) {
                    byte[] svgBytes = svgStream.readAllBytes();
                    String svgContent = new String(svgBytes, "UTF-8");
                    return convertEmojiToPngImg(emoji, svgContent, svgCodePoint);
                } catch (Exception e) {
                    log.info("读取SVG文件失败: {}, 使用fallback", classpathPath);
                    return convertEmojiToFallbackImg(emoji);
                }
            } else {
                log.info("SVG文件不存在: {} (代码点: {}), emoji: {}, 使用fallback处理", classpathPath, svgCodePoint, emoji);
                return convertEmojiToFallbackImg(emoji);
            }

        } catch (Exception e) {
            log.info("转换emoji失败: {}, 错误: {}", emoji, e.getMessage());
            return convertEmojiToFallbackImg(emoji);
        }
    }

    /**
     * 从SVG URL中提取代码点
     */
    private String extractCodePointFromUrl(String svgUrl) {
        if (svgUrl == null || svgUrl.isEmpty()) {
            return null;
        }
        
        // 格式: /static/twemoji/assets/svg/1f600.svg
        int lastSlash = svgUrl.lastIndexOf('/');
        int lastDot = svgUrl.lastIndexOf('.');
        
        if (lastSlash >= 0 && lastDot > lastSlash) {
            return svgUrl.substring(lastSlash + 1, lastDot);
        }
        
        return null;
    }

    /**
     * 将emoji转换为base64 PNG img标签
     */
    protected String convertEmojiToPngImg(String emoji, String svgContent, String svgCodePoint) {
        try {
            String optimizedSvg = optimizeSvgForPdfAdvanced(svgContent);

            ByteArrayOutputStream pngOutput = new ByteArrayOutputStream();
            PNGTranscoder transcoder = new PNGTranscoder();

            transcoder.addTranscodingHint(SVGAbstractTranscoder.KEY_WIDTH, 16f);
            transcoder.addTranscodingHint(SVGAbstractTranscoder.KEY_HEIGHT, 16f);

            TranscoderInput input = new TranscoderInput(
                    new StringReader(optimizedSvg));
            TranscoderOutput output = new TranscoderOutput(
                    pngOutput);
            transcoder.transcode(input, output);
            byte[] pngBytes = pngOutput.toByteArray();

            String base64Png = Base64.getEncoder().encodeToString(pngBytes);
            String dataUri = "data:image/png;base64," + base64Png;

            String imgStyle = "width:1em;height:1em;vertical-align:baseline;display:inline-block;position:relative;top:0.125em;";
            String imgTag = String.format("<img src=\"%s\" alt=\"%s\" class=\"is-emoji\" style=\"%s\" />", dataUri, emoji, imgStyle);

            log.debug("成功转换emoji [{}] -> base64 PNG", emoji);
            return imgTag;

        } catch (Exception e) {
            log.debug("SVG 转 PNG 失败，使用原始字符: {}", e.getMessage());
            return emoji;
        }
    }

    /**
     * 高级SVG优化，专门针对PDF渲染器
     */
    protected String optimizeSvgForPdfAdvanced(String svgContent) {
        String optimized = svgContent;

        if (!optimized.contains("xmlns=\"http://www.w3.org/2000/svg\"")) {
            optimized = optimized.replaceFirst("<svg", "<svg xmlns=\"http://www.w3.org/2000/svg\"");
        }

        optimized = optimized.replaceFirst("<svg[^>]*>",
                "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"1em\" height=\"1em\" viewBox=\"0 0 36 36\">");

        optimized = optimized.replaceAll("\\s+style=\"[^\"]*\"", "");
        optimized = optimized.replaceAll("\\s+class=\"[^\"]*\"", "");

        return optimized.trim();
    }

    /**
     * 将HTML body追加到模板
     */
    protected String appendTemplateToHtml(String body) {
        String htmlString = ResourceUtil.readUtf8Str("shell_template/md_to_html.html");
        String result = htmlString.replace("{{body}}", body);
        try {
            URI uri = new URI(templateUrl);
            HttpURLConnection connection = (HttpURLConnection) uri.toURL().openConnection();
            connection.setRequestMethod("GET");
            connection.setConnectTimeout(10000);
            connection.setReadTimeout(30000);
            connection.setRequestProperty("User-Agent", "Mozilla/5.0 (compatible; McpPlatformApi/1.0)");

            int responseCode = connection.getResponseCode();
            if (responseCode == HttpURLConnection.HTTP_OK) {
                StringBuilder content = new StringBuilder();
                try (BufferedReader reader = new BufferedReader(
                        new InputStreamReader(connection.getInputStream(), "UTF-8"))) {
                    String line;
                    while ((line = reader.readLine()) != null) {
                        content.append(line).append("\n");
                    }
                }

                String htmlTemplate = content.toString();
                log.info("成功下载HTML模板，大小: {} 字符", htmlTemplate.length());
                return htmlTemplate.replace("{{body}}", body);
            } else {
                log.error("下载HTML模板失败，HTTP状态码: {}", responseCode);
                return result;
            }
        } catch (IOException | URISyntaxException e) {
            log.error("下载HTML模板时发生异常: {}", e.getMessage(), e);
            return result;
        }
    }

    /**
     * 将HTML转换为XML兼容格式
     */
    protected String sanitizeHtmlForXml(String html) {
        if (html == null) {
            return html;
        }

        String sanitized = html
                .replaceAll("&(?!(?:amp|lt|gt|quot|apos|nbsp|#\\d+|#x[0-9a-fA-F]+);)", "&amp;")
                .replaceAll("<img([^>]*?)(?<!/)>", "<img$1 />")
                .replaceAll("<meta\\s+([^>]*?)\\s*/?>", "<meta $1 />")
                .replaceAll("<input\\s+([^>]*?)\\s*/?>", "<input $1 />")
                .replaceAll("<br\\s*/?>", "<br />")
                .replaceAll("<hr\\s*/?>", "<hr />")
                .replaceAll("<area\\s+([^>]*?)\\s*/?>", "<area $1 />")
                .replaceAll("<base\\s+([^>]*?)\\s*/?>", "<base $1 />")
                .replaceAll("<col\\s+([^>]*?)\\s*/?>", "<col $1 />")
                .replaceAll("<embed\\s+([^>]*?)\\s*/?>", "<embed $1 />")
                .replaceAll("<link\\s+([^>]*?)\\s*/?>", "<link $1 />")
                .replaceAll("<param\\s+([^>]*?)\\s*/?>", "<param $1 />")
                .replaceAll("<source\\s+([^>]*?)\\s*/?>", "<source $1 />")
                .replaceAll("<track\\s+([^>]*?)\\s*/?>", "<track $1 />")
                .replaceAll("<wbr\\s*/?>", "<wbr />");

        log.debug("HTML XML兼容性处理完成");
        return sanitized;
    }

    /**
     * 清理临时文件和目录
     */
    protected void cleanupTempFiles(Path tempDir) {
        if (tempDir != null && Files.exists(tempDir)) {
            try {
                log.info("清理临时文件: {}", tempDir);
                try (var pathStream = Files.walk(tempDir)) {
                    pathStream.sorted(Comparator.reverseOrder())
                            .forEach(path -> {
                                try {
                                    Files.deleteIfExists(path);
                                } catch (IOException e) {
                                    log.warn("删除临时文件失败: {}, 错误: {}", path, e.getMessage());
                                }
                            });
                }
                log.info("✓ 临时文件清理完成");
            } catch (Exception e) {
                log.warn("⚠ 清理临时文件时发生异常: {}", e.getMessage());
            }
        }
    }

    /**
     * 获取emoji的Unicode代码点
     */
    protected String getEmojiCodePoint(String emoji) {
        if (emoji == null || emoji.isEmpty()) {
            return null;
        }

        StringBuilder codePoints = new StringBuilder();
        for (int i = 0; i < emoji.length();) {
            int codePoint = emoji.codePointAt(i);

            if (isVariationSelector(codePoint) || isZeroWidthJoiner(codePoint)) {
                i += Character.charCount(codePoint);
                continue;
            }

            if (!codePoints.isEmpty()) {
                codePoints.append("-");
            }
            codePoints.append(Integer.toHexString(codePoint).toLowerCase());
            i += Character.charCount(codePoint);
        }

        return !codePoints.isEmpty() ? codePoints.toString() : null;
    }

    /**
     * 判断是否为变体选择器
     */
    protected boolean isVariationSelector(int codePoint) {
        return (codePoint >= 0xFE00 && codePoint <= 0xFE0F) ||
                (codePoint >= 0xE0100 && codePoint <= 0xE01EF);
    }

    /**
     * 判断是否为零宽连接符
     */
    protected boolean isZeroWidthJoiner(int codePoint) {
        return codePoint == 0x200D;
    }

    /**
     * Fallback方法：为无法找到SVG的emoji生成简单的文本替代
     */
    protected String convertEmojiToFallbackImg(String emoji) {
        return convertEmojiToTextFallback(emoji);
    }

    /**
     * 文本fallback - 使用Unicode字符和CSS样式
     */
    protected String convertEmojiToTextFallback(String emoji) {
        return emoji;
    }

    /**
     * 处理HTML中的WebP图片，转换为PNG格式（base64）
     * 注意：如果 native 库不可用，将移除所有 WebP 图片标签（避免 openhtmltopdf 渲染时触发 UnsatisfiedLinkError）
     * openhtmltopdf不支持WebP格式，需要转换为PNG
     */
    protected String processWebPImagesInHtml(String html) {
        log.info("开始处理HTML中的WebP图片，原始HTML长度: {}", html.length());

        // 先检查 native 库是否可用（避免后续频繁失败）
        boolean webpNativeAvailable = checkWebPNativeLibraryAvailable();
        if (!webpNativeAvailable) {
            log.warn("WebP native 库不可用，移除所有 WebP 图片标签（避免 PDF 渲染错误）");
            // 移除所有包含 .webp 的 img 标签，用 alt 属性文本或空字符串替换
            Pattern webpImgPattern = Pattern.compile(
                "<img\\s+[^>]*?src\\s*=\\s*[\"'][^\"']*?\\.webp[^\"']*?[\"'][^>]*?>(?:(?:(?!</img>).)*</img>)?",
                Pattern.CASE_INSENSITIVE | Pattern.DOTALL
            );
            String processedHtml = webpImgPattern.matcher(html).replaceAll(matchResult -> {
                // 尝试提取 alt 属性作为替换文本
                String imgTag = matchResult.group();
                Pattern altPattern = Pattern.compile("alt\\s*=\\s*[\"']([^\"']*?)[\"']", Pattern.CASE_INSENSITIVE);
                Matcher altMatcher = altPattern.matcher(imgTag);
                if (altMatcher.find()) {
                    return altMatcher.group(1); // 返回 alt 文本
                }
                return ""; // 如果没有 alt，返回空字符串
            });
            log.info("WebP图片处理完成：移除了所有 WebP 图片标签（native 库不可用），处理后HTML长度: {}", processedHtml.length());
            return processedHtml;
        }

        // 匹配所有img标签，更灵活的正则，支持各种属性顺序和URL编码
        // 匹配src属性包含.webp的img标签（不区分大小写，支持URL编码）
        Pattern imgPattern = Pattern.compile(
            "(<img\\s+[^>]*?src\\s*=\\s*[\"'])([^\"']*?)(\\.webp)([^\"']*?)([\"'][^>]*?>)",
            Pattern.CASE_INSENSITIVE | Pattern.DOTALL
        );

        StringBuilder result = new StringBuilder();
        Matcher matcher = imgPattern.matcher(html);
        int webpCount = 0;
        int successCount = 0;
        int skippedCount = 0;

        int lastEnd = 0;
        while (matcher.find()) {
            String beforeSrc = matcher.group(1);
            String urlBeforeExt = matcher.group(2);
            String webpExt = matcher.group(3);
            String urlAfterExt = matcher.group(4);
            String afterSrc = matcher.group(5);
            
            // 重建完整的URL（处理URL编码的情况）
            String srcUrl = urlBeforeExt + webpExt + urlAfterExt;

            webpCount++;
            log.debug("找到WebP图片 #{}: {}", webpCount, srcUrl);

            try {
                // URL解码处理（如果URL是编码的）
                String decodedUrl = srcUrl;
                try {
                    decodedUrl = java.net.URLDecoder.decode(srcUrl, "UTF-8");
                    if (!decodedUrl.equals(srcUrl)) {
                        log.debug("URL解码: {} -> {}", srcUrl, decodedUrl);
                    }
                } catch (Exception decodeEx) {
                    log.debug("URL解码失败，使用原始URL: {}", decodeEx.getMessage());
                }
                
                // 下载WebP图片
                byte[] webpBytes = downloadImage(decodedUrl);
                
                // 转换为PNG
                byte[] pngBytes = convertWebPToPng(webpBytes);
                
                // 生成base64 data URI
                String base64Png = Base64.getEncoder().encodeToString(pngBytes);
                String dataUri = "data:image/png;base64," + base64Png;
                
                // 替换src属性
                String newImgTag = beforeSrc + dataUri + afterSrc;
                
                result.append(html, lastEnd, matcher.start());
                result.append(newImgTag);
                lastEnd = matcher.end();
                
                successCount++;
                log.info("成功转换WebP图片 #{}: {} -> base64 PNG ({} bytes)", 
                    webpCount, srcUrl, pngBytes.length);
                
            } catch (Exception e) {
                // 转换失败，检查是否是 native 库错误
                skippedCount++;
                String errorMsg = e.getMessage();
                boolean isNativeError = errorMsg != null && 
                    (errorMsg.contains("native") || 
                     errorMsg.contains("No native library") ||
                     errorMsg.contains("UnsatisfiedLinkError"));
                
                if (isNativeError) {
                    log.warn("WebP图片 #{} [{}] 转换失败（native库不可用），移除图片标签", 
                        webpCount, srcUrl);
                    // native 库错误时，移除图片标签（用 alt 文本或空字符串替换）
                    String imgTag = html.substring(matcher.start(), matcher.end());
                    Pattern altPattern = Pattern.compile("alt\\s*=\\s*[\"']([^\"']*?)[\"']", Pattern.CASE_INSENSITIVE);
                    Matcher altMatcher = altPattern.matcher(imgTag);
                    String replacement = altMatcher.find() ? altMatcher.group(1) : "";
                    result.append(html, lastEnd, matcher.start());
                    result.append(replacement);
                } else {
                    log.warn("WebP图片 #{} [{}] 转换失败: {}, 移除图片标签", 
                        webpCount, srcUrl, errorMsg);
                    // 其他错误也移除图片标签，避免后续渲染问题
                    String imgTag = html.substring(matcher.start(), matcher.end());
                    Pattern altPattern = Pattern.compile("alt\\s*=\\s*[\"']([^\"']*?)[\"']", Pattern.CASE_INSENSITIVE);
                    Matcher altMatcher = altPattern.matcher(imgTag);
                    String replacement = altMatcher.find() ? altMatcher.group(1) : "";
                    result.append(html, lastEnd, matcher.start());
                    result.append(replacement);
                }
                lastEnd = matcher.end();
            }
        }
        
        result.append(html, lastEnd, html.length());

        log.info("WebP图片处理完成，共找到{}个WebP图片，成功转换{}个，跳过{}个，处理后HTML长度: {}",
                webpCount, successCount, skippedCount, result.length());
        return result.toString();
    }
    
    /**
     * 检查 WebP native 库是否可用
     * 通过尝试解码一个最小的 WebP 文件头来判断
     */
    private boolean checkWebPNativeLibraryAvailable() {
        try {
            // 创建一个最小的 WebP 文件头（仅用于检测库是否可用）
            // 这不是一个完整的 WebP 文件，但如果库可用，至少不会抛出 UnsatisfiedLinkError
            byte[] testWebPHeader = new byte[] {
                'R', 'I', 'F', 'F',  // RIFF
                0x00, 0x00, 0x00, 0x00,  // size (0 for test)
                'W', 'E', 'B', 'P'   // WEBP
            };
            
            java.io.ByteArrayInputStream testBais = new java.io.ByteArrayInputStream(testWebPHeader);
            // 尝试读取（即使失败也没关系，只要不抛出 UnsatisfiedLinkError 就说明库可用）
            javax.imageio.ImageIO.read(testBais);
            return true;
        } catch (java.lang.UnsatisfiedLinkError | java.lang.NoClassDefFoundError e) {
            log.debug("WebP native 库不可用: {}", e.getClass().getSimpleName());
            return false;
        } catch (Exception e) {
            // 其他异常（如格式错误）说明库本身是可用的，只是文件格式不对
            // 这是正常的，说明库加载成功
            return true;
        }
    }

    /**
     * 下载网络图片
     */
    protected byte[] downloadImage(String imageUrl) throws Exception {
        try {
            URI uri = new URI(imageUrl);
            HttpURLConnection connection = (HttpURLConnection) uri.toURL().openConnection();
            
            // 设置请求头（模拟浏览器）
            connection.setRequestMethod("GET");
            connection.setConnectTimeout(10000);
            connection.setReadTimeout(30000);
            connection.setRequestProperty("User-Agent", 
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36");
            
            int responseCode = connection.getResponseCode();
            if (responseCode == HttpURLConnection.HTTP_OK) {
                try (InputStream inputStream = connection.getInputStream();
                     ByteArrayOutputStream outputStream = new ByteArrayOutputStream()) {
                    
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

    /**
     * 将WebP图片转换为PNG格式
     * 注意：此方法依赖 native 库，如果 native 库不可用，将抛出异常
     * 调用方应该捕获异常并采用降级策略（如跳过转换）
     */
    protected byte[] convertWebPToPng(byte[] webpBytes) throws Exception {
        // 检查是否是 WebP 格式（通过文件头判断）
        if (!isWebPFormat(webpBytes)) {
            throw new Exception("不是有效的 WebP 格式");
        }
        
        try {
            // 使用ImageIO读取WebP图片（通过webp-imageio插件支持）
            java.io.ByteArrayInputStream bais = new java.io.ByteArrayInputStream(webpBytes);
            java.awt.image.BufferedImage image = javax.imageio.ImageIO.read(bais);
            
            if (image == null) {
                throw new Exception("无法解码WebP图片：ImageIO返回null");
            }
            
            // 转换为PNG
            ByteArrayOutputStream pngOutput = new ByteArrayOutputStream();
            boolean success = javax.imageio.ImageIO.write(image, "PNG", pngOutput);
            
            if (!success) {
                throw new Exception("PNG编码失败");
            }
            
            log.debug("成功转换WebP到PNG: {} bytes -> {} bytes", webpBytes.length, pngOutput.size());
            return pngOutput.toByteArray();
            
        } catch (java.lang.UnsatisfiedLinkError | java.lang.NoClassDefFoundError e) {
            // native 库不可用
            log.error("WebP native 库不可用（{}），无法转换: {}", 
                e.getClass().getSimpleName(), e.getMessage());
            throw new Exception("WebP native 库不可用: " + e.getMessage(), e);
        } catch (Exception e) {
            // 其他错误
            String errorMsg = e.getMessage();
            if (errorMsg != null && errorMsg.contains("No native library")) {
                log.error("WebP native 库未找到，无法转换: {}", errorMsg);
                throw new Exception("WebP native 库未找到: " + errorMsg, e);
            }
            log.error("WebP转PNG失败: {}", errorMsg);
            throw new Exception("WebP格式转换失败: " + errorMsg, e);
        }
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


    
}

