import pandas as pd
urls=[
'https://raw.githubusercontent.com/DanMcInerney/mma-ai/main/data/raw/ufcstats/competitions.csv',
'https://raw.githubusercontent.com/DanMcInerney/mma-ai/main/data/raw/ufcstats/individuals.csv'
]
for u in urls:
    df=pd.read_csv(u,nrows=3,low_memory=False)
    print('\nURL',u)
    print('COLS',list(df.columns))
    print(df.head(2).to_dict(orient='records'))
