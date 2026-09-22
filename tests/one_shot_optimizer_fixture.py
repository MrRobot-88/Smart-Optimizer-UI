#!/usr/bin/env python3
import importlib.util, os, sys
os.environ.setdefault('RADARR_KEY','fixture-not-real')
os.environ.setdefault('SONARR_KEY','fixture-not-real')
os.environ.setdefault('SMART_OPTIMIZER_MANUAL_TARGET','0')

def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod

rad=load('radoneshot',sys.argv[1]); son=load('sononeshot',sys.argv[2])

s=rad.blank_state(); s['movies']={'10':{'search_cycles':1,'last_search':1}, '20':{'search_cycles':0}}
assert rad.auto_processed_movie_ids(s)=={10}
rad.mark_movie_searched(s,20)
assert rad.auto_processed_movie_ids(s)=={10,20}
assert s['movies']['20']['auto_processed'] is True
assert s['movies']['20']['search_cycles']==1
print('FIXTURE PASS: Radarr existing searched movies migrate to permanent one-shot gate')

movie={
 'id':20,'hasFile':True,'title':'Fixture','year':2026,'qualityProfileId':4,'tmdbId':1,
 'movieFile':{
   'size':10*1024**3,
   'quality':{'quality':{'resolution':1080}},
   'mediaInfo':{'videoCodec':'x265','audioChannels':2.0,'videoDynamicRange':'SDR'}
 }
}
assert rad.movie_item(movie,s,set()) is None
manual=dict(s); manual['movies']=dict(s['movies']); manual['movies'].pop('20',None); manual['auto_processed_movie_ids']=[10]
item=rad.movie_item(movie,manual,set())
assert item and item['movie_id']==20
print('FIXTURE PASS: Radarr automatic is blocked forever while manual retry bypasses')

ss=son.blank_state()
ss['series_queue']=[{'series_id':1,'series_title':'A'},{'series_id':2,'series_title':'B'},{'series_id':3,'series_title':'C'}]
ss['series_cursor']=2
ss['work_queue']=[
 {'series_id':1,'episode_id':11,'season':1,'episode':1},
 {'series_id':2,'episode_id':21,'season':1,'episode':1},
 {'series_id':2,'episode_id':22,'season':1,'episode':2},
]
ss['work_cursor']=2
son.LIVE=False
son.reconcile_auto_processed_series(ss)
assert son.auto_processed_series_ids(ss)=={1}
ss['work_cursor']=3
son.reconcile_auto_processed_series(ss)
assert son.auto_processed_series_ids(ss)=={1,2}
print('FIXTURE PASS: Sonarr series closes only after its automatic work is fully consumed')

son.get=lambda path: (
 {'id':2,'title':'B','qualityProfileId':4} if path.startswith('/series/') else
 {'id':21,'seriesId':2,'hasFile':True,'episodeFileId':210,'seasonNumber':1,'episodeNumber':1,'title':'E1'} if path.startswith('/episode/21') else
 {'id':210,'size':900*1024**2,'quality':{'quality':{'resolution':1080}},'mediaInfo':{'videoCodec':'x265','audioChannels':2.0,'videoDynamicRange':'SDR'}} if path.startswith('/episodefile/') else
 []
)
entry={'series_id':2,'series_title':'B','episode_id':21,'season':1,'episode':1}
ss['episodes']={'21':{'search_cycles':1,'auto_processed':True}}
assert son.item_from_queue_entry(entry,set(),ss) is None
assert son.item_from_queue_entry(entry,set(),ss,ignore_search_history=True) is not None
print('FIXTURE PASS: Sonarr automatic episode retry blocked; explicit manual retry bypasses')

assert 3 not in son.auto_processed_series_ids(ss)
print('FIXTURE PASS: New series remains eligible until its first automatic traversal')
print('ALL ONE-SHOT FIXTURES PASSED')
