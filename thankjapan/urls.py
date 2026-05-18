
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from thank_japan_app.views import robots_txt
from django.views.generic import TemplateView
from thank_japan_app.views import sitemap_view
from django.http import JsonResponse


app_name = "thank_japan_app"
urlpatterns = [
    path('manifest.json', TemplateView.as_view(template_name='manifest.json', content_type='application/json')),
    path('sw.js', TemplateView.as_view(template_name='sw.js', content_type='application/javascript')),
    
    


    path('.well-known/assetlinks.json', lambda r: JsonResponse([
    {
        "relation": ["delegate_permission/common.handle_all_urls"],
        "target": {
            "namespace": "android_app",
            "package_name": "com.thankjapan.www.twa",
            "sha256_cert_fingerprints": ["1E:CA:F1:51:07:5D:AE:E3:8E:AB:D3:04:A9:4A:E8:9B:E3:6A:80:6D:9B:80:3E:F2:D6:10:56:76:42:D0:E4:80"]
        }
    }
    ], safe=False)),
    
    path('robots.txt', robots_txt),
    path('kanrisha/', admin.site.urls),
    
    path('accounts/', include('allauth.urls')),
    
    path('', include('thank_japan_app.urls')),
   
    path('sitemap.xml', sitemap_view, name='sitemap'),
    path('ja/sitemap.xml', sitemap_view),
    path('vi/sitemap.xml', sitemap_view),
    path('th/sitemap.xml', sitemap_view),
    path('ko/sitemap.xml', sitemap_view),
    path('zh-hant/sitemap.xml', sitemap_view),
    path('zh-cn/sitemap.xml', sitemap_view),
    path('it/sitemap.xml', sitemap_view),
    path('fr/sitemap.xml', sitemap_view),
    path('es-es/sitemap.xml', sitemap_view),
    path('es-mx/sitemap.xml', sitemap_view),
    path('de/sitemap.xml', sitemap_view),
    path('pt/sitemap.xml', sitemap_view),
    path('pt-br/sitemap.xml', sitemap_view),
    path('en-in/sitemap.xml', sitemap_view),
]

urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)