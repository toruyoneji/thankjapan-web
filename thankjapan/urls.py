
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from thank_japan_app.views import robots_txt
from django.views.generic import TemplateView

app_name = "thank_japan_app"
urlpatterns = [
    path('robots.txt', robots_txt),
    path('kanrisha/', admin.site.urls),
    path('support/', include('payment.urls')),
    path('', include('thank_japan_app.urls')),
   
    
    path('sitemap.xml', TemplateView.as_view(
        template_name='sitemap.xml', content_type='application/xml')),
    
    path('de/sitemap.xml', TemplateView.as_view(
        template_name='sitemap.xml', content_type='application/xml')),
    
    path('en-in/sitemap.xml', TemplateView.as_view(
        template_name='sitemap.xml', content_type='application/xml')),
    
    path('es-es/sitemap.xml', TemplateView.as_view(
        template_name='sitemap.xml', content_type='application/xml')),
    
    path('es-mx/sitemap.xml', TemplateView.as_view(
        template_name='sitemap.xml', content_type='application/xml')),
    
    path('fr/sitemap.xml', TemplateView.as_view(
        template_name='sitemap.xml', content_type='application/xml')),
    
    path('it/sitemap.xml', TemplateView.as_view(
        template_name='sitemap.xml', content_type='application/xml')),
    
    path('ja/sitemap.xml', TemplateView.as_view(
        template_name='sitemap.xml', content_type='application/xml')),
    
    path('ko/sitemap.xml', TemplateView.as_view(
        template_name='sitemap.xml', content_type='application/xml')),
    
    path('pt-br/sitemap.xml', TemplateView.as_view(
        template_name='sitemap.xml', content_type='application/xml')),
    
    path('pt/sitemap.xml', TemplateView.as_view(
        template_name='sitemap.xml', content_type='application/xml')),
    
    path('th/sitemap.xml', TemplateView.as_view(
        template_name='sitemap.xml', content_type='application/xml')),
    
    path('vi/sitemap.xml', TemplateView.as_view(
        template_name='sitemap.xml', content_type='application/xml')),
    
    path('zh-hant/sitemap.xml', TemplateView.as_view(
        template_name='sitemap.xml', content_type='application/xml')),
   
]

urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)