from django.urls import path
from .views import (TopView, KiyakuView, FoodView, NatureView, FashionView, 
                    CultureView, CookView, AppliancesView, AnimalView,
                    ImgDetailView, BuildingView, FlowerView, HouseholdItemsView,
                    SportsView, WorkView, CompanyFormView, GameView, answer, LegalNoticeView,
                    PrivacyPolicy)


urlpatterns = [
    #top page
    path("", TopView.as_view(), name="toppage"),
    path("game/", GameView.as_view(), name="game"),
    path("answer/<int:pk>", answer, name="answer"),
    
    #category list page
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

    #user look page
    path("detail/<int:pk>/", ImgDetailView.as_view(), name="detail"),
    path("riyoukiyaku/", KiyakuView.as_view(), name="riyoukiyaku"),
    path("infomation/", CompanyFormView.as_view(), name="infomationpage"),
    
    path('legal-notice/', LegalNoticeView.as_view(), name='legal_notice'),
    path('privacy-policy', PrivacyPolicy.as_view(), name="privacy_policy"),
]