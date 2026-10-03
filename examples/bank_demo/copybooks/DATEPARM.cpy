      *================================================================*
      * DATEPARM - PARAMETER BLOCK FOR CALL 'DATEUTIL'                 *
      *   V = VALIDATE DATE-1          B = DAYS FROM DATE-1 TO DATE-2  *
      *   A = DATE-1 PLUS DAYS         W = DAY OF WEEK (1=MON..7=SUN)  *
      *   RETURN CODE 00 OK, 10 BAD DATE, 20 OUT OF RANGE, 99 BAD FUNC *
      *================================================================*
       01  DATE-PARMS.
           05 DP-FUNCTION          PIC X.
              88 DP-VALIDATE       VALUE 'V'.
              88 DP-DAYS-BETWEEN   VALUE 'B'.
              88 DP-ADD-DAYS       VALUE 'A'.
              88 DP-DAY-OF-WEEK    VALUE 'W'.
           05 DP-DATE-1            PIC 9(8).
           05 DP-DATE-2            PIC 9(8).
           05 DP-DAYS              PIC S9(7).
           05 DP-RESULT-DATE       PIC 9(8).
           05 DP-DOW               PIC 9.
           05 DP-RETURN-CODE       PIC 99.
