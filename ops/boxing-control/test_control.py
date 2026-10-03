import unittest
from control import request
class Commands(unittest.TestCase):
 def test_allow(self):
  for action in ['status','start-existing-validation','compare']:
   self.assertEqual(request('{"action":"'+action+'","request_id":"cloud-test-0001"}')['action'],action)
 def test_reject(self):
  for text in ['{}','[]','null','{"action":"shell","request_id":"cloud-test-0001"}','{"action":"status","request_id":"$(id)"}','{"action":"status","request_id":"../escape"}','{"action":"status","request_id":"cloud-test-0001","path":"/etc/passwd"}','{"action":"status","action":"compare","request_id":"cloud-test-0001"}','{"action":"status","request_id":1}','{"action":"status","request_id":"a"}','x'*513]:
   with self.assertRaises((ValueError,TypeError)):request(text)
if __name__=='__main__':unittest.main()
