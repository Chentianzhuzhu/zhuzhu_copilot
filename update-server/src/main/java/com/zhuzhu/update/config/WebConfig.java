package com.zhuzhu.update.config;

import com.zhuzhu.update.security.AdminAuthInterceptor;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.CacheControl;
import org.springframework.web.servlet.HandlerInterceptor;
import org.springframework.web.servlet.config.annotation.InterceptorRegistry;
import org.springframework.web.servlet.config.annotation.ResourceHandlerRegistry;
import org.springframework.web.servlet.config.annotation.ViewControllerRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

import java.nio.file.Paths;
import java.time.Duration;

@Configuration
public class WebConfig implements WebMvcConfigurer {

    private final AdminAuthInterceptor adminAuth;

    @Value("${upload.dir:./uploads}")
    private String uploadDir;

    public WebConfig(AdminAuthInterceptor adminAuth) {
        this.adminAuth = adminAuth;
    }

    /** 后台接口全部要求 Bearer token（登录接口内部放行） */
    @Override
    public void addInterceptors(InterceptorRegistry registry) {
        registry.addInterceptor(adminAuth)
                .addPathPatterns("/admin/api/**");

        // 官网页面与 SEO 文件不缓存：HTML 里引用的是带内容哈希的资源 URL，
        // 一旦 HTML 被缓存而与资源版本错配，就会出现样式/脚本失效甚至整屏空白。
        registry.addInterceptor(new HandlerInterceptor() {
            @Override
            public boolean preHandle(HttpServletRequest request, HttpServletResponse response, Object handler) {
                response.setHeader("Cache-Control", "no-cache");
                return true;
            }
        }).addPathPatterns("/", "/index.html", "/gallery", "/download", "/faq",
                "/robots.txt", "/sitemap.xml", "/site.webmanifest");
    }

    /** 上传的安装包与官网图片通过 /uploads/** 对外访问 */
    @Override
    public void addResourceHandlers(ResourceHandlerRegistry registry) {
        String loc = Paths.get(uploadDir).toAbsolutePath().normalize().toUri().toString();
        if (!loc.endsWith("/")) {
            loc += "/";   // ResourceHttpRequestHandler 要求 location 以 / 结尾
        }
        registry.addResourceHandler("/uploads/**").addResourceLocations(loc);

        // css/ 与 js/ 下的资源在模板里一律带内容哈希版本号（?v=xxxx），
        // 因此可以放心长缓存：内容一变 URL 就变，不会出现新旧版本混用。
        //
        // 注意：addResourceHandler("/css/**") 会把 /css/ 前缀当作模式匹配掉，
        // 因此 location 必须指到子目录，否则会去 static/site.css 找文件而 404。
        //
        // 缓存时长刻意有限（7 天）而非一年 immutable：模板引用的 URL 都带版本号，
        // 长缓存是安全的；但万一有无版本号的访问（工具、旧链接）被 CDN 缓存，
        // 有限时长能保证它自动过期，不会永久停留在旧内容上。
        CacheControl assets = CacheControl.maxAge(Duration.ofDays(7)).cachePublic();
        registry.addResourceHandler("/css/**")
                .addResourceLocations("classpath:/static/css/")
                .setCacheControl(assets);
        registry.addResourceHandler("/js/**")
                .addResourceLocations("classpath:/static/js/")
                .setCacheControl(assets);

        // 管理后台是静态 HTML，无法在服务端拼版本号，因此改用「不缓存」：
        // 否则浏览器/代理可能继续用旧 admin.js，而新页面结构已变 —— 表现为后台一片空白。
        registry.addResourceHandler("/admin/**")
                .addResourceLocations("classpath:/static/admin/")
                .setCacheControl(CacheControl.noStore());
    }

    /** /admin 与 /admin/ 都跳到后台首页（转发不改变浏览器地址栏） */
    @Override
    public void addViewControllers(ViewControllerRegistry registry) {
        registry.addViewController("/admin").setViewName("forward:/admin/index.html");
        registry.addViewController("/admin/").setViewName("forward:/admin/index.html");
    }
}
