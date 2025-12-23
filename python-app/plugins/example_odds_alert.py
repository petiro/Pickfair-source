"""
Example Plugin: Odds Alert
Invia notifica quando le quote superano una soglia.
"""

PLUGIN_NAME = "Odds Alert"
PLUGIN_VERSION = "1.0.0"
PLUGIN_AUTHOR = "Pickfair Team"
PLUGIN_DESCRIPTION = "Notifica quando le quote di una selezione superano la soglia impostata"

# Variabili globali
app = None
api = None
settings = {}


def register(application):
    """Registra il plugin."""
    global app, api, settings
    app = application
    
    from plugin_manager import PluginAPI
    api = PluginAPI(app.plugin_manager, PLUGIN_NAME)
    
    # Carica impostazioni salvate
    settings = api.load_data('settings.json', {
        'enabled': True,
        'min_odds': 3.0,
        'max_odds': 10.0,
        'notify_on_change': True
    })
    
    api.log(f"Registrato! Soglia quote: {settings['min_odds']} - {settings['max_odds']}")


def unregister(application):
    """Disregistra il plugin."""
    global app, api
    
    if api:
        api.log("Disregistrato!")
    
    app = None
    api = None


def check_odds(runners):
    """
    Controlla le quote e notifica se superano la soglia.
    
    Args:
        runners: Lista di runner con quote
    """
    if not api or not settings.get('enabled'):
        return
    
    min_odds = settings.get('min_odds', 3.0)
    max_odds = settings.get('max_odds', 10.0)
    
    for runner in runners:
        name = runner.get('runnerName', 'N/A')
        back_price = runner.get('ex', {}).get('availableToBack', [{}])[0].get('price', 0)
        lay_price = runner.get('ex', {}).get('availableToLay', [{}])[0].get('price', 0)
        
        if back_price and min_odds <= back_price <= max_odds:
            api.log(f"ALERT: {name} quota BACK {back_price} nella soglia!")
        
        if lay_price and min_odds <= lay_price <= max_odds:
            api.log(f"ALERT: {name} quota LAY {lay_price} nella soglia!")


def set_threshold(min_odds, max_odds):
    """
    Imposta la soglia delle quote.
    
    Args:
        min_odds: Quota minima
        max_odds: Quota massima
    """
    global settings
    settings['min_odds'] = min_odds
    settings['max_odds'] = max_odds
    
    if api:
        api.save_data('settings.json', settings)
        api.log(f"Soglia aggiornata: {min_odds} - {max_odds}")


def enable():
    """Abilita il monitoraggio."""
    global settings
    settings['enabled'] = True
    if api:
        api.save_data('settings.json', settings)


def disable():
    """Disabilita il monitoraggio."""
    global settings
    settings['enabled'] = False
    if api:
        api.save_data('settings.json', settings)
