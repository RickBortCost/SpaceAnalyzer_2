"""Validate an explicit block-to-channel map. Never guess from block order."""

def validate_mapping(reader,channels,samples_per_channel):
    if not isinstance(channels,dict) or not 1<=len(channels)<=64:raise ValueError('Mappatura: da 1 a 64 canali')
    seen=set()
    for name,indexes in channels.items():
        if not isinstance(name,str) or not name or not isinstance(indexes,list) or not indexes:raise ValueError('Mappatura canale non valida')
        if indexes!=sorted(indexes):raise ValueError('Blocchi del canale non in ordine di file')
        count=0
        for i in indexes:
            if type(i) is not int or not 0<=i<len(reader.blocks) or i in seen:raise ValueError('Blocco mancante, duplicato o fuori intervallo')
            seen.add(i);count+=reader.blocks[i].samples
        if count!=samples_per_channel:raise ValueError(f'Mappatura {name}: {count} campioni invece di {samples_per_channel}')
    if len(seen)!=len(reader.blocks):raise ValueError('Mappatura incompleta: alcuni blocchi non appartengono ad alcun canale')
    return True
