from django.urls import path
from .views import (TopView, TopViewFR, TopViewIT, TopViewPT, TopViewZHHANT,TopViewKO, TopViewESES,
                    KiyakuView, FoodView, NatureView, FashionView, 
                    CultureView, CookView, AppliancesView, AnimalView,
                    CategoryDetailView, BuildingView, FlowerView, HouseholdItemsView,LiveView,
                    SportsView, WorkView, CompanyFormView, game_start, game_play,
                    game_answer, game_result, game_restart, player_login, player_logout, delete_player_confirm,
                    player_register, delete_player, LegalNoticeView,PrivacyPolicy, contact_view, contact_thanks)


urlpatterns = [
    #top page
    path("", TopView.as_view(), name="toppage"),
    path('fr/', TopViewFR.as_view(), name='toppagefr'),
    path('it/', TopViewIT.as_view(), name='toppageit'),
    path('pt/', TopViewPT.as_view(), name='toppagept'),
    path('zh-hant/', TopViewZHHANT.as_view(), name='toppagezhHANT'),
    path('ko/', TopViewKO.as_view(), name='toppageko'),
    path('es-es/', TopViewESES.as_view(), name='toppageesES'),
    
    
    #game
    path('game/start/', game_start, name='game_start'),
    path('game/play/', game_play, name='game_play'),
    path('game/answer/<int:pk>/', game_answer, name='game_answer'),
    path('game/result/', game_result, name='game_result'),
    path('game/restart/', game_restart, name='game_restart'),
    path('logout/', player_logout, name='logout_player'),
    path('player/delete/confirm', delete_player, name='delete_player'),
    path('player/delete/', delete_player_confirm, name='delete_player_confirm'),
    path('register/', player_register, name='player_register'),
    path('login/', player_login, name='player_login'),
    
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
    path("live/", LiveView.as_view(), name="livepage"),

    #company page
    path("riyoukiyaku/", KiyakuView.as_view(), name="riyoukiyaku"),
    path("infomation/", CompanyFormView.as_view(), name="infomationpage"),
    path('contact/', contact_view, name='contact'),
    path('contact/thanks/', contact_thanks, name='contact_thanks'),
    path('legal-notice/', LegalNoticeView.as_view(), name='legal_notice'),
    path('privacy-policy/', PrivacyPolicy.as_view(), name="privacy_policy"),
    
    #user look page
    path('<str:category>/<slug:slug>/', CategoryDetailView.as_view(), name='category_detail'),
    
]