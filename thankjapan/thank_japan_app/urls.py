from django.urls import path
from .views import (TopView, KiyakuView, FoodView, NatureView, FashionView, 
                    CultureView, CookView, AppliancesView, AnimalView,
                    ImgDetailView, BuildingView, FlowerView, HouseholdItemsView,
                    SportsView, WorkView, UserDeleteFormView, UserDeleteView)


urlpatterns = [
    path("", TopView.as_view(), name="toppage"),
    path("food/", FoodView.as_view(), name="foodpage"),
    path("nature/", NatureView.as_view(), name="naturepage"),
    path("fashion/", FashionView.as_view(), name="fashionpage"),
    path("culture/", CultureView.as_view(), name="culturepage"),
    path("cook/", CookView.as_view(), name="cookpage"),
    path("appliances/", AppliancesView.as_view(), name="appliancespage"),
    path("animal/", AnimalView.as_view(), name="animalpage"),
    path("building/", BuildingView.as_view(), name="buildingpage"),
    path("flower/", FlowerView.as_view(), name="flowerpage"),
    path("householditems/", HouseholdItemsView.as_view(), name="householditemspage"),
    path("sports/", SportsView.as_view(), name="sportspage"),
    path("work/", WorkView.as_view(), name="workpage"),

    path("detail/<int:pk>/", ImgDetailView.as_view(), name="detail"),
    path("riyoukiyaku/", KiyakuView.as_view(), name="riyoukiyaku"),
    path("delete/", UserDeleteFormView.as_view(), name="userdeleteform"),
    path("delete/post", UserDeleteView.as_view(), name="userdelete"),
]