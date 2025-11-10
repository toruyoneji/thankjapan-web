from django.urls import path
from .views import (TopView, TopViewFR, TopViewIT, TopViewPT, TopViewZHHANT,TopViewKO, TopViewESES,
                    TopViewDE, TopViewTH, TopViewPTBR, TopViewESMX, TopViewENIN, TopViewJA, TopViewVI,
                    KiyakuView, FoodView, NatureView, FashionView, 
                    CultureView, CookView, AppliancesView, AnimalView,
                    CategoryDetailView, BuildingView, FlowerView, HouseholdItemsView,LiveView,
                    SportsView, WorkView, CompanyFormView, game_start, game_play,
                    game_answer, game_result, game_restart, player_login, player_logout, delete_player_confirm,
                    player_register, delete_player, LegalNoticeView,PrivacyPolicy, contact_view, contact_thanks,
                    JapanFoodView,JapanFoodDEView, JapanFoodENINView, JapanFoodESESView,
                    JapanFoodESMXView, JapanFoodFRView, JapanFoodITView, JapanFoodJAView ,JapanFoodKOView,
                    JapanFoodPTBRView, JapanFoodPTView, JapanFoodTHView, JapanFoodVIView, JapanFoodZHHANTView,
                    JapanCultureView, JapanCultureDEView, JapanCultureENINView, JapanCultureESESView,
                    JapanCultureESMXView, JapanCultureFRView,JapanCultureITView, JapanCultureJAView,JapanCultureKOView,
                    JapanCulturePTBRView,JapanCulturePTView,JapanCultureTHView,JapanCultureVIView,
                    JapanCultureZHHANTView,)


urlpatterns = [
    #top page
    path("", TopView.as_view(), name="toppage"),
    path("ja/", TopViewJA.as_view(), name="toppageja"),
    path("vi/", TopViewVI.as_view(), name="toppagevi"),
    path('fr/', TopViewFR.as_view(), name='toppagefr'),
    path('it/', TopViewIT.as_view(), name='toppageit'),
    path('pt/', TopViewPT.as_view(), name='toppagept'),
    path('zh-hant/', TopViewZHHANT.as_view(), name='toppagezhHANT'),
    path('ko/', TopViewKO.as_view(), name='toppageko'),
    path('es-es/', TopViewESES.as_view(), name='toppageesES'),
    path('de/', TopViewDE.as_view(), name='toppagede'),
    path('th/', TopViewTH.as_view(), name='toppageth'),
    path('pt-br/', TopViewPTBR.as_view(), name='toppageptBR'),
    path('es-mx/', TopViewESMX.as_view(), name='toppageesMX'),
    path('en-in/', TopViewENIN.as_view(), name='toppageenIN'),
    
    #japanfood
    path('japanfood/', JapanFoodView.as_view(), name='japanfood'),
    path('japanfood/de/', JapanFoodDEView.as_view(), name='japanfoodde'),
    path('japanfood/en-in/', JapanFoodENINView.as_view(), name='japanfoodenIN'),
    path('japanfood/es-es/', JapanFoodESESView.as_view(), name='japanfoodesES'),
    path('japanfood/es-mx/', JapanFoodESMXView.as_view(), name='japanfoodesMX'),
    path('japanfood/fr/', JapanFoodFRView.as_view(), name='japanfoodfr'),
    path('japanfood/it/', JapanFoodITView.as_view(), name='japanfoodit'),
    path('japanfood/ja/', JapanFoodJAView.as_view(), name='japanfoodja'),
    path('japanfood/ko/', JapanFoodKOView.as_view(), name='japanfoodko'),
    path('japanfood/pt-br/', JapanFoodPTBRView.as_view(), name='japanfoodptBR'),
    path('japanfood/pt/', JapanFoodPTView.as_view(), name='japanfoodpt'),
    path('japanfood/th/', JapanFoodTHView.as_view(), name='japanfoodth'),
    path('japanfood/vi/', JapanFoodVIView.as_view(), name='japanfoodvi'),
    path('japanfood/zh-hant/', JapanFoodZHHANTView.as_view(), name='japanfoodzhHANT'),
    
    
    #japanculture
    path('japanculture/', JapanCultureView.as_view(), name='japanculture'),
    path('japanculture/de/', JapanCultureDEView.as_view(), name='japanculturede'),
    path('japanculture/en-in/', JapanCultureENINView.as_view(), name='japancultureenIN'),
    path('japanculture/es-es/', JapanCultureESESView.as_view(), name='japancultureesES'),
    path('japanculture/es-mx/', JapanCultureESMXView.as_view(), name='japancultureesMX'),
    path('japanculture/fr/', JapanCultureFRView.as_view(), name='japanculturefr'),
    path('japanculture/it/', JapanCultureITView.as_view(), name='japancultureit'),
    path('japanculture/ja/', JapanCultureJAView.as_view(), name='japancultureja'),
    path('japanculture/ko/', JapanCultureKOView.as_view(), name='japancultureko'),
    path('japanculture/pt-br/', JapanCulturePTBRView.as_view(), name='japancultureptBR'),
    path('japanculture/pt/', JapanCulturePTView.as_view(), name='japanculturept'),
    path('japanculture/th/', JapanCultureTHView.as_view(), name='japancultureth'),
    path('japanculture/vi/', JapanCultureVIView.as_view(), name='japanculturevi'),
    path('japanculture/zh-hant/', JapanCultureZHHANTView.as_view(), name='japanculturezhHANT'),
    
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