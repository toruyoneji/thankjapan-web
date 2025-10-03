from django.urls import path
from .views import (TopView, KiyakuView, FoodView, NatureView, FashionView, 
                    CultureView, CookView, AppliancesView, AnimalView,
                    ImgDetailView, BuildingView, FlowerView, HouseholdItemsView,
                    SportsView, WorkView, CompanyFormView, game_start, game_play,
                    game_answer, game_result, game_restart, logout_player, 
                    delete_player, LegalNoticeView,PrivacyPolicy, contact_view, contact_thanks)


urlpatterns = [
    #top page
    path("", TopView.as_view(), name="toppage"),
    #game
    path('game/start/', game_start, name='game_start'),
    path('game/play/', game_play, name='game_play'),
    path('game/answer/<int:pk>/', game_answer, name='game_answer'),
    path('game/result/', game_result, name='game_result'),
    path('game/restart/', game_restart, name='game_restart'),
    path('logout/', logout_player, name='logout_player'),
    path('player/delete/', delete_player, name='delete_player'),
    
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
    path('contact/', contact_view, name='contact'),
    path('contact/thanks/', contact_thanks, name='contact_thanks'),
    
    path('legal-notice/', LegalNoticeView.as_view(), name='legal_notice'),
    path('privacy-policy/', PrivacyPolicy.as_view(), name="privacy_policy"),
]