
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from thank_japan_app.views import robots_txt

app_name = "thank_japan_app"
urlpatterns = [
    path('robots.txt', robots_txt),
    path('kanrisha/', admin.site.urls),
    path('', include('thank_japan_app.urls')),
    path('payment/', include('payment.urls')),
   
]

urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)