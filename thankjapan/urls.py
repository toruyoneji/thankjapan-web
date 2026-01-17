
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from thank_japan_app.views import robots_txt
from django.views.generic import TemplateView
from thank_japan_app.views import sitemap_view

app_name = "thank_japan_app"
urlpatterns = [
    path('robots.txt', robots_txt),
    path('kanrisha/', admin.site.urls),
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