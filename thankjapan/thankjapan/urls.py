
from django.contrib import admin
from django.urls import path, include

app_name = "thank_japan_app"
urlpatterns = [
    path('admin/', admin.site.urls),
    path('thankjp/', include('thank_japan_app.urls')),
]
