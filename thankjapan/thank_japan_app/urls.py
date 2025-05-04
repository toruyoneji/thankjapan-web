from django.urls import path
from .views import TopView, FoodView, NatureView, FashionView, CultureView, CookView, AppliancesView, AnimalView

urlpatterns = [
    path("", TopView.as_view(), name="toppage"),
    path("food/", FoodView.as_view(), name="foodpage"),
    path("nature/", NatureView.as_view(), name="naturepage"),
    path("fashion/", FashionView.as_view(), name="fashionpage"),
    path("culture/", CultureView.as_view(), name="culturepage"),
    path("cook/", CookView.as_view(), name="cookpage"),
    path("appliances/", AppliancesView.as_view(), name="appliancespage"),
    path("animal/", AnimalView.as_view(), name="animalpage"),

]