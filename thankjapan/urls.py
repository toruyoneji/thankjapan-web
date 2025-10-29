
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
   
]

urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)