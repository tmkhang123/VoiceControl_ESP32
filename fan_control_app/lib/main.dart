import 'dart:async';
import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:socket_io_client/socket_io_client.dart' as io;
import 'package:fl_chart/fl_chart.dart';

const String serverUrl = 'http://YOUR_SERVER_IP:5000'; // ← thay bằng IP máy chạy server.py
const int waveformPoints = 100;

void main() {
  runApp(const FanControlApp());
}

class FanControlApp extends StatelessWidget {
  const FanControlApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Quat Thong Minh',
      debugShowCheckedModeBanner: false,
      theme: ThemeData.dark().copyWith(
        scaffoldBackgroundColor: const Color(0xFF111111),
      ),
      home: const FanControlPage(),
    );
  }
}

class FanControlPage extends StatefulWidget {
  const FanControlPage({super.key});

  @override
  State<FanControlPage> createState() => _FanControlPageState();
}

class _FanControlPageState extends State<FanControlPage> {
  int _level = 0;
  String _name = 'TAT';
  Color _color = const Color(0xFF333333);
  bool _loading = false;
  String _lastCommand = '';
  Timer? _statusTimer;
  late io.Socket _socket;
  final List<double> _waveform = List.filled(waveformPoints, 0.0, growable: true);

  @override
  void initState() {
    super.initState();
    _fetchStatus();
    _statusTimer = Timer.periodic(const Duration(seconds: 2), (_) => _fetchStatus());
    _connectSocket();
  }

  void _connectSocket() {
    _socket = io.io(serverUrl, <String, dynamic>{
      'transports': ['websocket'],
      'autoConnect': true,
    });

    _socket.on('mic_batch', (data) {
      final List<dynamic> values = data['values'];
      const double maxVal = 32768.0;
      const double gain = 8.0;
      setState(() {
        for (final v in values) {
          _waveform.removeAt(0);
          _waveform.add(((v as num).toDouble() / maxVal * gain).clamp(-1.0, 1.0));
        }
      });
    });

    _socket.on('command', (data) {
      setState(() {
        _level = data['level'];
        _name = data['name'];
        _color = _parseColor(data['color'] ?? '#333333');
        _lastCommand = _name;
      });
    });
  }

  @override
  void dispose() {
    _statusTimer?.cancel();
    _socket.dispose();
    super.dispose();
  }

  Future<void> _fetchStatus() async {
    try {
      final res = await http
          .get(Uri.parse('$serverUrl/api/status'))
          .timeout(const Duration(seconds: 3));
      if (res.statusCode == 200) {
        final data = jsonDecode(res.body);
        setState(() {
          _level = data['level'];
          _name = data['name'];
          _color = _parseColor(data['color']);
        });
      }
    } catch (_) {}
  }

  Color _parseColor(String hex) {
    final h = hex.replaceAll('#', '');
    return Color(int.parse('FF$h', radix: 16));
  }

  Future<void> _callApi(String path) async {
    setState(() => _loading = true);
    try {
      final res = await http
          .post(Uri.parse('$serverUrl$path'))
          .timeout(const Duration(seconds: 3));
      if (res.statusCode == 200) {
        final data = jsonDecode(res.body);
        setState(() {
          _level = data['level'];
          _name = data['name'];
          _color = _parseColor(data['color']);
        });
      }
    } catch (_) {} finally {
      setState(() => _loading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              // Title
              const Text(
                'QUAT THONG MINH',
                textAlign: TextAlign.center,
                style: TextStyle(
                  fontSize: 22,
                  fontWeight: FontWeight.bold,
                  color: Color(0xFFFFCC00),
                  letterSpacing: 3,
                ),
              ),
              const SizedBox(height: 16),

              // Trạng thái
              Container(
                padding: const EdgeInsets.symmetric(vertical: 20),
                decoration: BoxDecoration(
                  color: _color.withValues(alpha: 0.12),
                  borderRadius: BorderRadius.circular(16),
                  border: Border.all(color: _color, width: 2),
                ),
                child: Column(
                  children: [
                    Icon(Icons.air, size: 48, color: _color),
                    const SizedBox(height: 8),
                    Text(
                      _name,
                      style: TextStyle(
                        fontSize: 26,
                        fontWeight: FontWeight.bold,
                        color: _color,
                      ),
                    ),
                    if (_lastCommand.isNotEmpty)
                      Padding(
                        padding: const EdgeInsets.only(top: 4),
                        child: Text(
                          'Lenh: $_lastCommand',
                          style: const TextStyle(
                              color: Colors.white54, fontSize: 12),
                        ),
                      ),
                  ],
                ),
              ),

              const SizedBox(height: 16),

              // Waveform
              Container(
                height: 100,
                padding: const EdgeInsets.all(10),
                decoration: BoxDecoration(
                  color: const Color(0xFF1E1E1E),
                  borderRadius: BorderRadius.circular(12),
                ),
                child: LineChart(
                  LineChartData(
                    gridData: const FlGridData(show: false),
                    titlesData: const FlTitlesData(show: false),
                    borderData: FlBorderData(show: false),
                    minY: -1,
                    maxY: 1,
                    lineBarsData: [
                      LineChartBarData(
                        spots: List.generate(
                          _waveform.length,
                          (i) => FlSpot(i.toDouble(), _waveform[i]),
                        ),
                        isCurved: false,
                        color: const Color(0xFF00BFFF),
                        barWidth: 1,
                        dotData: const FlDotData(show: false),
                      ),
                    ],
                  ),
                ),
              ),

              const SizedBox(height: 16),

              // Nút cấp độ
              Row(
                children: [
                  _levelBtn('TAT', 0, const Color(0xFF555555)),
                  const SizedBox(width: 8),
                  _levelBtn('SO 1', 1, Colors.red),
                  const SizedBox(width: 8),
                  _levelBtn('SO 2', 2, Colors.amber),
                  const SizedBox(width: 8),
                  _levelBtn('SO 3', 3, Colors.green),
                ],
              ),

              const SizedBox(height: 12),

              // Nút tăng/giảm
              Row(
                children: [
                  Expanded(
                    child: _actionBtn(
                      'GIAM',
                      Icons.arrow_downward,
                      Colors.orange,
                      () => _callApi('/api/giam'),
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: _actionBtn(
                      'TANG TOC',
                      Icons.arrow_upward,
                      const Color(0xFF4CAF50),
                      () => _callApi('/api/tang'),
                    ),
                  ),
                ],
              ),

              const Spacer(),

              if (_loading)
                const Center(
                  child: CircularProgressIndicator(
                    color: Color(0xFFFFCC00),
                    strokeWidth: 2,
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _levelBtn(String label, int level, Color color) {
    final isActive = _level == level;
    return Expanded(
      child: SizedBox(
        height: 72,
        child: ElevatedButton(
          onPressed: _loading ? null : () => _callApi('/api/set/$level'),
          style: ElevatedButton.styleFrom(
            backgroundColor: isActive ? color : color.withValues(alpha: 0.15),
            foregroundColor: Colors.white,
            side: BorderSide(color: color, width: 2),
            shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(12),
            ),
            padding: EdgeInsets.zero,
          ),
          child: Text(
            label,
            style: const TextStyle(fontSize: 12, fontWeight: FontWeight.bold),
            textAlign: TextAlign.center,
          ),
        ),
      ),
    );
  }

  Widget _actionBtn(String label, IconData icon, Color color, VoidCallback onTap) {
    return SizedBox(
      height: 56,
      child: ElevatedButton.icon(
        onPressed: _loading ? null : onTap,
        icon: Icon(icon, size: 18),
        label: Text(label,
            style: const TextStyle(fontSize: 15, fontWeight: FontWeight.bold)),
        style: ElevatedButton.styleFrom(
          backgroundColor: color,
          foregroundColor: Colors.white,
          shape:
              RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
        ),
      ),
    );
  }
}
