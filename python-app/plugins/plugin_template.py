"""
Plugin Template per Pickfair
Copia questo file e modificalo per creare il tuo plugin.
"""

# Metadati obbligatori del plugin
PLUGIN_NAME = "Nome Plugin"
PLUGIN_VERSION = "1.0.0"
PLUGIN_AUTHOR = "Tuo Nome"
PLUGIN_DESCRIPTION = "Descrizione del plugin"

# Variabili globali del plugin
app = None
api = None


def register(application):
    """
    Chiamato quando il plugin viene abilitato.
    
    Args:
        application: L'istanza dell'applicazione Pickfair
    """
    global app, api
    app = application
    
    # Ottieni l'API del plugin per interagire con l'app
    from plugin_manager import PluginAPI
    api = PluginAPI(app.plugin_manager, PLUGIN_NAME)
    
    # Log di avvio
    api.log("Plugin registrato!")
    
    # Registra hook per eventi
    # api.register_hook('on_market_loaded', on_market_loaded)
    # api.register_hook('on_bet_placed', on_bet_placed)
    # api.register_hook('on_odds_update', on_odds_update)


def unregister(application):
    """
    Chiamato quando il plugin viene disabilitato.
    Pulisci le risorse qui.
    """
    global app, api
    
    if api:
        api.log("Plugin disregistrato!")
    
    app = None
    api = None


# === HOOK FUNCTIONS (opzionali) ===

def on_market_loaded(market_data):
    """
    Chiamato quando un nuovo mercato viene caricato.
    
    Args:
        market_data: Dati del mercato (dict)
    """
    pass


def on_bet_placed(bet_data):
    """
    Chiamato quando una scommessa viene piazzata.
    
    Args:
        bet_data: Dati della scommessa (dict)
    """
    pass


def on_odds_update(runners):
    """
    Chiamato quando le quote vengono aggiornate.
    
    Args:
        runners: Lista di runner con quote aggiornate
    """
    pass


# === FUNZIONI PERSONALIZZATE ===

def my_custom_function():
    """
    Le tue funzioni personalizzate.
    Puoi usare api.get_current_market(), api.log(), api.save_data(), ecc.
    """
    if api:
        market = api.get_current_market()
        if market:
            api.log(f"Mercato attuale: {market.get('marketName', 'N/A')}")


# === ESEMPIO: Salvare e caricare dati ===

def save_plugin_settings(settings_dict):
    """Salva impostazioni del plugin."""
    if api:
        api.save_data('settings.json', settings_dict)


def load_plugin_settings():
    """Carica impostazioni del plugin."""
    if api:
        return api.load_data('settings.json', {'default_key': 'default_value'})
    return {}
