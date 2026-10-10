"""Prepublication 7.1 Book/MusicGame adapter for the historical Native v1 surface."""

from .native_archive_retrieval import NativeArchiveProvider


class NativeBookMusic71Provider(NativeArchiveProvider):
    def __init__(self, artifact, manifest):
        super().__init__(artifact, manifest)
        if (self.manifest.get('product_accepted') is not False
                or self.manifest['release'].get('snapshot_id')
                != 'anime-game-data:7.1:9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99'
                or self.manifest['release'].get('full_7_1') is not False):
            raise ValueError('Book/MusicGame 7.1 prepublication authority')

    def capability(self):
        result = super().capability()
        result['eligible_runs'][0]['product_accepted'] = False
        result['eligible_runs'][0]['research_accepted'] = False
        return result
